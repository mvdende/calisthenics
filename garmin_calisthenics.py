#!/usr/bin/env python3
"""
Genereer afwisselende calisthenics-workouts en zet ze automatisch in Garmin Connect.

Waarom dit werkt: elke oefening wordt opgebouwd met een `category` + `exerciseName`
die letterlijk in Garmins eigen oefeningenbibliotheek staat. Daardoor schrijft je
epix de juiste oefeningnaam in het FIT-bestand, en komt die via de Garmin-Strava
koppeling automatisch in je Strava-workoutlogboek terecht. Verzin je zelf een naam,
dan slaat Garmin die leeg op en zie je in Strava alsnog "Unknown".

Gebruik:
    pip install garminconnect pydantic

    export GARMIN_EMAIL="jij@example.com"
    export GARMIN_PASSWORD="..."

    python garmin_calisthenics.py --days 7 --dry-run   # eerst kijken
    python garmin_calisthenics.py --days 7             # aanmaken + inplannen
    python garmin_calisthenics.py --days 7 --push      # ook direct naar het horloge

De eerste keer vraagt Garmin om een MFA-code; daarna wordt het token gecached
in ~/.garminconnect en hoef je niet opnieuw in te loggen.

Elke run schrijft ook plan.json (--export-json om het pad te wijzigen, leeg
om over te slaan). Dat bestand leest de webapp-begeleider (workout.html):
dezelfde Move-objecten die naar Garmin gaan, dus wat op je horloge staat en
wat de app toont kan nooit uit elkaar lopen. --dry-run werkt zonder Garmin-
login, dus je kunt plan.json bijwerken zonder GARMIN_EMAIL/PASSWORD te zetten.

Wil je plan.json juist baseren op wat er al écht in je Garmin-agenda staat
(bijvoorbeeld omdat je iets met de hand hebt aangepast in Garmin Connect),
gebruik dan --from-calendar: dat genereert niets, maar leest de agenda en
de bijbehorende workout-stappen terug en schrijft die naar plan.json.

    python garmin_calisthenics.py --from-calendar --days 7

Dit vereist wél GARMIN_EMAIL/PASSWORD (de agenda staat immers op Garmins
servers), maar dat blijft op je eigen machine — de webapp praat nooit
rechtstreeks met Garmin.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from dataclasses import dataclass
from datetime import date, timedelta

from garminconnect import Garmin, exercises as catalog
from garminconnect.workout import (
    ConditionType,
    ExecutableStep,
    StepType,
    StrengthWorkout,
    TargetType,
    WorkoutSegment,
    create_repeat_group,
    create_strength_exercise_step,
    create_strength_rest_step,
)

TOKEN_DIR = os.path.expanduser("~/.garminconnect")


# --------------------------------------------------------------------------
# Oefeningenpool: alleen bodyweight, alleen keys die in Garmins catalogus staan.
# Wordt bij het starten gevalideerd tegen garminconnect.exercises.
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Move:
    category: str
    exercise: str
    label: str
    kind: str = "reps"       # "reps" of "time"
    target: int = 12         # aantal reps, of seconden bij kind="time"


WARMUP = [
    Move("WARM_UP", "WALKING_HIGH_KNEES", "Walking High Knees", "time", 40),
    Move("CARDIO", "JUMPING_JACKS", "Jumping Jacks", "reps", 30),
    Move("WARM_UP", "ARM_CIRCLES", "Arm Circles", "time", 30),
    Move("WARM_UP", "WALKOUT", "Walkout", "reps", 8),
    Move("WARM_UP", "CAT_CAMEL", "Cat Camel", "reps", 10),
    Move("WARM_UP", "GROINERS", "Groiners", "reps", 10),
]

PUSH = [
    Move("PUSH_UP", "PUSH_UP", "Push-up", "reps", 12),
    Move("PUSH_UP", "WIDE_HANDS_PUSH_UP", "Wide-hands Push-up", "reps", 12),
    Move("PUSH_UP", "DIAMOND_PUSH_UP", "Diamond Push-up", "reps", 10),
    Move("PUSH_UP", "INCLINE_PUSH_UP", "Incline Push-up", "reps", 15),
    Move("PUSH_UP", "PIKE_PUSH_UP", "Pike Push-up", "reps", 10),
    Move("PUSH_UP", "SHOULDER_TAPPING_PUSH_UP", "Shoulder Tapping Push-up", "reps", 10),
    Move("PUSH_UP", "SPIDERMAN_PUSH_UP", "Spiderman Push-up", "reps", 10),
    Move("PUSH_UP", "HAND_RELEASE_PUSH_UP", "Hand Release Push-up", "reps", 10),
    Move("PUSH_UP", "T_PUSH_UP", "T Push-up", "reps", 10),
    Move("TRICEPS_EXTENSION", "BENCH_DIP", "Bench Dip", "reps", 12),
]

LEGS = [
    Move("SQUAT", "AIR_SQUAT", "Air Squat", "reps", 20),
    Move("SQUAT", "PRISONER_SQUAT", "Prisoner Squat", "reps", 16),
    Move("SQUAT", "SUMO_SQUAT", "Sumo Squat", "reps", 16),
    Move("SQUAT", "BODY_WEIGHT_WALL_SQUAT", "Body-weight Wall Squat", "time", 45),
    Move("SQUAT", "SQUAT_JUMPS_IN_N_OUT", "Squat Jumps In N' Out", "reps", 12),
    Move("LUNGE", "LUNGE", "Lunge", "reps", 16),
    Move("LUNGE", "WALKING_LUNGE", "Walking Lunge", "reps", 16),
    Move("LUNGE", "SIDE_LUNGE", "Side Lunge", "reps", 14),
    Move("LUNGE", "CURTSY_LUNGE", "Curtsy Lunge", "reps", 14),
    Move("HIP_RAISE", "HIP_RAISE", "Hip Raise", "reps", 15),
    Move("HIP_RAISE", "SINGLE_LEG_HIP_RAISE", "Single-leg Hip Raise", "reps", 12),
    Move("HIP_RAISE", "MARCHING_HIP_RAISE", "Marching Hip Raise", "reps", 16),
    Move("CALF_RAISE", "CALF_RAISE", "Calf Raise", "reps", 20),
]

CORE = [
    Move("PLANK", "PLANK", "Plank", "time", 45),
    Move("PLANK", "SIDE_PLANK", "Side Plank", "time", 30),
    Move("PLANK", "MOUNTAIN_CLIMBER", "Mountain Climber", "reps", 24),
    Move("PLANK", "BEAR_CRAWL", "Bear Crawl", "time", 30),
    Move("PLANK", "PLANK_WITH_KNEE_TO_ELBOW", "Plank with Knee-to-Elbow", "reps", 16),
    Move("PLANK", "PLANK_WITH_LEG_LIFT", "Plank with Leg Lift", "reps", 14),
    Move("CRUNCH", "BICYCLE_CRUNCH", "Bicycle Crunch", "reps", 24),
    Move("CRUNCH", "FLUTTER_KICKS", "Flutter Kicks", "time", 40),
    Move("CRUNCH", "HOLLOW_ROCK", "Hollow Rock", "reps", 16),
    Move("SIT_UP", "SIT_UP", "Sit-up", "reps", 18),
    Move("SIT_UP", "V_UP", "V-up", "reps", 12),
    Move("CORE", "RUSSIAN_TWIST", "Russian Twist", "reps", 24),
    Move("LEG_RAISE", "LEG_RAISE", "Leg Raise", "reps", 14),
    Move("HYPEREXTENSION", "SUPERMAN_FROM_FLOOR", "Superman from Floor", "reps", 14),
]

CARDIO = [
    Move("TOTAL_BODY", "BURPEE", "Burpee", "reps", 12),
    Move("CARDIO", "SQUAT_JACKS", "Squat Jacks", "reps", 24),
    Move("CARDIO", "SPLIT_JACKS", "Split Jacks", "reps", 24),
    Move("CARDIO", "JUMPING_JACKS", "Jumping Jacks", "reps", 40),
    Move("PLANK", "PLANK_PIKE_JUMPS", "Plank Pike Jumps", "reps", 16),
]

ALL_MOVES = WARMUP + PUSH + LEGS + CORE + CARDIO
MOVE_BY_KEY = {(m.category, m.exercise): m for m in ALL_MOVES}

# Dagschema's: welke blokken en hoeveel oefeningen per blok.
# Houdt de sessie rond de 10-15 minuten.
SCHEMES = [
    ("Full body",   [(PUSH, 2), (LEGS, 2), (CORE, 1)]),
    ("Upper + core", [(PUSH, 3), (CORE, 2)]),
    ("Lower + core", [(LEGS, 3), (CORE, 2)]),
    ("Core + cardio", [(CORE, 3), (CARDIO, 2)]),
    ("Full body cardio", [(CARDIO, 2), (LEGS, 2), (PUSH, 1)]),
]


# --------------------------------------------------------------------------
# Bouwstenen
# --------------------------------------------------------------------------

def create_timed_set(move: Move, step_order: int, sets: int, rest_seconds: float):
    """Zoals create_strength_set, maar met een tijdsdoel in plaats van reps.

    Nodig voor planks, wall sits en andere holds. Garmin gebruikt hiervoor
    endCondition TIME op de oefeningstap zelf.
    """
    exercise = ExecutableStep(
        stepOrder=step_order + 1,
        stepType={"stepTypeId": StepType.INTERVAL, "stepTypeKey": "interval", "displayOrder": 3},
        endCondition={
            "conditionTypeId": ConditionType.TIME,
            "conditionTypeKey": "time",
            "displayOrder": 2,
            "displayable": True,
        },
        endConditionValue=float(move.target),
        targetType={
            "workoutTargetTypeId": TargetType.NO_TARGET,
            "workoutTargetTypeKey": "no.target",
            "displayOrder": 1,
        },
        category=move.category,
        exerciseName=move.exercise,
    )
    rest = create_strength_rest_step(rest_seconds, step_order + 2)
    return create_repeat_group(sets, [exercise, rest], step_order)


def build_block(move: Move, step_order: int, sets: int, rest_seconds: float):
    if move.kind == "time":
        return create_timed_set(move, step_order, sets, rest_seconds)
    exercise = create_strength_exercise_step(
        move.category, step_order + 1, move.target, exercise_name=move.exercise
    )
    rest = create_strength_rest_step(rest_seconds, step_order + 2)
    return create_repeat_group(sets, [exercise, rest], step_order)


def estimate_seconds(moves, sets, rest_seconds, warmup_moves):
    """Ruwe schatting: ongeveer 2 seconden per herhaling, plus rust."""
    total = 0
    for m in warmup_moves:
        total += (m.target if m.kind == "time" else m.target * 2) + 15
    for m in moves:
        work = m.target if m.kind == "time" else m.target * 2
        total += sets * (work + rest_seconds)
    return int(total)


def trim_to_duration(moves, sets, rest, warmup_moves, target_minutes, minimum=3):
    """Gooi oefeningen weg tot de sessie binnen de gewenste duur past."""
    while len(moves) > minimum and estimate_seconds(moves, sets, rest, warmup_moves) > target_minutes * 60:
        moves = moves[:-1]
    return moves


def pick_workout(day: date, rng: random.Random, warmup: bool, recent: list[Move]):
    """Kies schema en oefeningen; vermijd wat de afgelopen dagen al langskwam."""
    scheme_name, blocks = SCHEMES[day.toordinal() % len(SCHEMES)]

    warmup_moves = rng.sample(WARMUP, 2) if warmup else []

    chosen: list[Move] = []
    for pool, count in blocks:
        fresh = [m for m in pool if m not in chosen and m not in recent and m not in warmup_moves]
        if len(fresh) < count:  # pool te klein, val terug op alles behalve vandaag
            fresh = [m for m in pool if m not in chosen]
        chosen.extend(rng.sample(fresh, min(count, len(fresh))))

    name = f"Calisthenics {day.strftime('%d-%m')} · {scheme_name}"
    return name, scheme_name, warmup_moves, chosen


def build_workout(name, warmup_moves, moves, sets, rest):
    steps = []
    order = 1

    for m in warmup_moves:
        steps.append(build_block(m, order, 1, 10))
        order += 3

    for m in moves:
        steps.append(build_block(m, order, sets, rest))
        order += 3

    return StrengthWorkout(
        workoutName=name,
        estimatedDurationInSecs=estimate_seconds(moves, sets, rest, warmup_moves),
        workoutSegments=[
            WorkoutSegment(
                segmentOrder=1,
                sportType={"sportTypeId": 5, "sportTypeKey": "strength_training"},
                workoutSteps=steps,
            )
        ],
    )


# --------------------------------------------------------------------------
# JSON-export: hetzelfde weekplan, in het formaat dat de webapp leest.
# Dezelfde Move-objecten die naar Garmin gaan, dus horloge en telefoon
# kunnen onmogelijk uit de pas lopen.
# --------------------------------------------------------------------------

def move_to_json(m: Move, sets: int, rest: int | None = None) -> dict:
    d = {
        "name": m.label,
        "key": f"{m.category}/{m.exercise}",
        "kind": m.kind,
        "target": m.target,
        "sets": sets,
    }
    if rest is not None:
        d["rest"] = rest
    return d


def plan_to_json(plan, sets: int, rest: int) -> dict:
    return {
        "generated": date.today().isoformat(),
        "days": [
            {
                "date": day.isoformat(),
                "scheme": scheme,
                "estimated_minutes": max(1, round(workout.estimatedDurationInSecs / 60)),
                "warmup": [move_to_json(m, 1) for m in warmup_moves],
                "exercises": [move_to_json(m, sets, rest) for m in moves],
            }
            for day, _name, scheme, warmup_moves, moves, workout in plan
        ],
    }


# --------------------------------------------------------------------------
# --from-calendar: niet zelf iets verzinnen, maar teruglezen wat er al in
# de Garmin-agenda staat en dát exporteren. Robuuster dan lokaal opnieuw
# genereren, want de agenda is de bron van waarheid: als je een workout met
# de hand hebt aangepast in Garmin Connect, of een oude run per ongeluk
# dubbel hebt gedraaid, zie je hier gegarandeerd wat er echt gepland staat.
#
# Dit gebruikt twee endpoints van garminconnect die niet publiek
# gedocumenteerd zijn (net als de rest van deze bibliotheek): het
# calendar-service voor "wat staat er op deze dag" en het workout-service
# voor de volledige stappen. De vorm hieronder is die van workout.py's
# eigen builders (create_repeat_group / create_strength_exercise_step /
# create_strength_rest_step) — wat wij uploaden is wat we terugkrijgen.
# --------------------------------------------------------------------------

def humanize_exercise_name(raw: str) -> str:
    """Nette naam voor een category/exercise-combinatie die niet in onze
    eigen pools staat (bijvoorbeeld met de hand toegevoegd in Garmin Connect)."""
    words = [w for w in raw.replace("'", " ").split("_") if w]
    return " ".join(w.capitalize() for w in words) or "Oefening"


def parse_workout_steps(steps: list[dict]) -> tuple[list[dict], list[dict]]:
    """Zet Garmins workoutstappen om naar warmup/exercises in ons JSON-formaat."""
    warmup: list[dict] = []
    exercises: list[dict] = []

    for step in steps:
        if (step.get("stepType") or {}).get("stepTypeKey") != "repeat":
            continue
        sets = int(step.get("numberOfIterations") or 1)
        children = step.get("workoutSteps") or []
        ex_step = next((c for c in children if (c.get("stepType") or {}).get("stepTypeKey") == "interval"), None)
        rest_step = next((c for c in children if (c.get("stepType") or {}).get("stepTypeKey") == "rest"), None)
        if ex_step is None:
            continue

        category = ex_step.get("category") or ""
        exercise = ex_step.get("exerciseName") or ""
        condition = (ex_step.get("endCondition") or {}).get("conditionTypeKey")
        target = int(round(float(ex_step.get("endConditionValue") or 0)))
        kind = "time" if condition == "time" else "reps"

        move = MOVE_BY_KEY.get((category, exercise))
        label = move.label if move else humanize_exercise_name(exercise or category)

        entry = {"name": label, "key": f"{category}/{exercise}", "kind": kind, "target": target, "sets": sets}
        # Warmup vs. hoofdoefening is op de Garmin-stap zelf niet gemarkeerd.
        # build_workout() bouwt warmup altijd met 1 set en oefeningen met
        # args.sets (doorgaans 3) — dat is het enige betrouwbare onderscheid,
        # want dezelfde beweging (bv. Jumping Jacks) staat soms in zowel de
        # WARMUP- als de CARDIO-pool, dus de category/exercise-naam alleen
        # is geen betrouwbaar signaal.
        if sets <= 1:
            warmup.append(entry)
        else:
            if rest_step is not None:
                entry["rest"] = int(round(float(rest_step.get("endConditionValue") or 0)))
            exercises.append(entry)

    return warmup, exercises


def estimate_minutes_from_entries(warmup: list[dict], exercises: list[dict]) -> int:
    total = 0
    for e in warmup:
        total += (e["target"] if e["kind"] == "time" else e["target"] * 2) + 15
    for e in exercises:
        work = e["target"] if e["kind"] == "time" else e["target"] * 2
        total += e["sets"] * (work + e.get("rest", 0))
    return max(1, round(total / 60))


def scheme_from_workout_name(name: str) -> str:
    if " · " in name:
        return name.split(" · ", 1)[1]
    return name


def fetch_calendar_items(api: Garmin, year: int, month: int, cache: dict) -> list[dict]:
    key = (year, month)
    if key not in cache:
        cache[key] = api.get_scheduled_workouts(year, month)
    data = cache[key]
    items = data.get("calendarItems") if isinstance(data, dict) else data
    return items or []


def find_scheduled_workout_id(items: list[dict], date_str: str):
    for item in items:
        if item.get("date") == date_str and str(item.get("itemType", "")).lower() == "workout" and item.get("workoutId"):
            return item["workoutId"]
    return None


def run_from_calendar(args) -> None:
    email = os.environ.get("GARMIN_EMAIL")
    password = os.environ.get("GARMIN_PASSWORD")
    if not email or not password:
        sys.exit("Zet GARMIN_EMAIL en GARMIN_PASSWORD als omgevingsvariabelen (nodig om de agenda te lezen).")

    api = Garmin(email, password)
    try:
        api.login(TOKEN_DIR)
    except Exception:
        api.login()
        api.garth.dump(TOKEN_DIR)

    device_id = None
    if args.push:
        devices = api.get_devices()
        if devices:
            device_id = devices[0]["deviceId"]
            print(f"Horloge: {devices[0].get('productDisplayName', device_id)}\n")

    start = date.fromisoformat(args.start) if args.start else date.today() + timedelta(days=1)
    cache: dict = {}
    days_json = []

    for i in range(args.days):
        day = start + timedelta(days=i)
        items = fetch_calendar_items(api, day.year, day.month, cache)
        workout_id = find_scheduled_workout_id(items, day.isoformat())
        if workout_id is None:
            print(f"{day:%a %d-%m} — niets gevonden in de agenda, overgeslagen", file=sys.stderr)
            continue

        detail = api.get_workout_by_id(workout_id)
        try:
            steps = detail["workoutSegments"][0]["workoutSteps"]
        except (KeyError, IndexError, TypeError):
            print(f"{day:%a %d-%m} — onverwachte vorm voor workout {workout_id}, overgeslagen", file=sys.stderr)
            continue

        warmup, exercises = parse_workout_steps(steps)
        scheme = scheme_from_workout_name(detail.get("workoutName") or "Calisthenics")
        raw_secs = detail.get("estimatedDurationInSecs")
        minutes = round(raw_secs / 60) if raw_secs else estimate_minutes_from_entries(warmup, exercises)
        minutes = max(1, minutes)

        days_json.append({
            "date": day.isoformat(), "scheme": scheme, "estimated_minutes": minutes,
            "warmup": warmup, "exercises": exercises,
        })

        print(f"\n{day:%a %d-%m} — {scheme} (~{minutes} min, uit agenda)")
        for e in warmup:
            unit = "s" if e["kind"] == "time" else "x"
            print(f"    warm-up  {e['name']}: {e['sets']} × {e['target']}{unit}")
        for e in exercises:
            unit = "s" if e["kind"] == "time" else "x"
            print(f"    {e['sets']} × {e['target']}{unit:<2} {e['name']}   [{e['key']}]")

        if device_id:
            api.push_workout_to_device(workout_id, device_id)

    if not days_json:
        sys.exit("\nNiets gevonden in de agenda voor deze periode — plan.json niet aangepast.")

    if args.dry_run:
        print("\n(dry-run: plan.json is niet geschreven)")
        return

    out_path = args.export_json or "plan.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"generated": date.today().isoformat(), "days": days_json}, f, ensure_ascii=False, indent=2)
    print(f"\n✓ Weekplan uit de Garmin-agenda geschreven naar {out_path}")


# --------------------------------------------------------------------------
# Validatie: elke key moet in Garmins catalogus staan
# --------------------------------------------------------------------------

def validate_pools() -> None:
    valid = {(e["category"], e["exercise"]) for e in catalog.EXERCISES}
    problems = []
    for pool in (WARMUP, PUSH, LEGS, CORE, CARDIO):
        for m in pool:
            if (m.category, m.exercise) not in valid:
                problems.append(f"{m.category}/{m.exercise} ({m.label})")
    if problems:
        print("Deze oefeningen staan NIET in Garmins bibliotheek en zouden als", file=sys.stderr)
        print("'Unknown' in Strava eindigen:", file=sys.stderr)
        for p in problems:
            print("  -", p, file=sys.stderr)
        sys.exit(1)


# --------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=7, help="aantal dagen vooruit plannen (default 7)")
    ap.add_argument("--start", default=None, help="startdatum YYYY-MM-DD (default: morgen)")
    ap.add_argument("--sets", type=int, default=3, help="sets per oefening (default 3)")
    ap.add_argument("--rest", type=int, default=30, help="rust tussen sets in seconden (default 30)")
    ap.add_argument("--minutes", type=int, default=15, help="maximale duur per sessie (default 15)")
    ap.add_argument("--no-warmup", action="store_true", help="warming-up overslaan")
    ap.add_argument("--push", action="store_true", help="workouts direct naar je horloge sturen")
    ap.add_argument("--dry-run", action="store_true", help="alleen tonen, niets uploaden")
    ap.add_argument("--seed", type=int, default=None, help="vaste seed voor reproduceerbare selectie")
    ap.add_argument(
        "--export-json", default="plan.json", metavar="PAD",
        help="schrijf het weekplan ook als JSON voor de webapp-begeleider (default plan.json, leeg om over te slaan)",
    )
    ap.add_argument(
        "--from-calendar", action="store_true",
        help="niets genereren: lees wat er al in je Garmin-agenda staat en schrijf dát naar plan.json "
             "(negeert --sets/--rest/--minutes/--no-warmup/--seed, vereist GARMIN_EMAIL/PASSWORD)",
    )
    args = ap.parse_args()

    if args.from_calendar:
        run_from_calendar(args)
        return

    validate_pools()

    start = date.fromisoformat(args.start) if args.start else date.today() + timedelta(days=1)
    rng = random.Random(args.seed if args.seed is not None else start.toordinal())

    plan = []
    recent: list[Move] = []
    for i in range(args.days):
        day = start + timedelta(days=i)
        name, scheme, warmup_moves, moves = pick_workout(day, rng, not args.no_warmup, recent)
        moves = trim_to_duration(moves, args.sets, args.rest, warmup_moves, args.minutes)
        recent = (recent + moves)[-8:]   # laatste ~2 dagen onthouden
        workout = build_workout(name, warmup_moves, moves, args.sets, args.rest)
        plan.append((day, name, scheme, warmup_moves, moves, workout))

    for day, name, scheme, warmup_moves, moves, workout in plan:
        mins = workout.estimatedDurationInSecs // 60
        print(f"\n{day:%a %d-%m} — {scheme} (~{mins} min)")
        for m in warmup_moves:
            unit = "s" if m.kind == "time" else "x"
            print(f"    warm-up  {m.label}: 1 × {m.target}{unit}")
        for m in moves:
            unit = "s" if m.kind == "time" else "x"
            print(f"    {args.sets} × {m.target}{unit:<2} {m.label}   [{m.category}/{m.exercise}]")

    if args.export_json:
        with open(args.export_json, "w", encoding="utf-8") as f:
            json.dump(plan_to_json(plan, args.sets, args.rest), f, ensure_ascii=False, indent=2)
        print(f"\n✓ Weekplan voor de webapp geschreven naar {args.export_json}")

    if args.dry_run:
        print("\n(dry-run: er is niets naar Garmin gestuurd)")
        return

    email = os.environ.get("GARMIN_EMAIL")
    password = os.environ.get("GARMIN_PASSWORD")
    if not email or not password:
        sys.exit("Zet GARMIN_EMAIL en GARMIN_PASSWORD als omgevingsvariabelen.")

    api = Garmin(email, password)
    try:
        api.login(TOKEN_DIR)
    except Exception:
        api.login()
        api.garth.dump(TOKEN_DIR)

    device_id = None
    if args.push:
        devices = api.get_devices()
        if devices:
            device_id = devices[0]["deviceId"]
            print(f"\nHorloge: {devices[0].get('productDisplayName', device_id)}")

    print()
    for day, name, _scheme, _w, _m, workout in plan:
        result = api.upload_strength_workout(workout)
        workout_id = result.get("workoutId")
        api.schedule_workout(workout_id, day.isoformat())
        if device_id:
            api.push_workout_to_device(workout_id, device_id)
        print(f"✓ {day.isoformat()}  {name}  (id {workout_id})")

    print("\nKlaar. Sync je horloge en start de workout vanuit je agenda.")


if __name__ == "__main__":
    main()
