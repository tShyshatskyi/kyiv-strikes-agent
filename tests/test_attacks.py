"""Stage 1: attacks instead of 09:00 days, and the code checks around the matcher."""

import pytest

from pipeline import attacks, validate
from pipeline.graph import _send_back, public_note, public_summary


def alert(start, end, kind="drone"):
    from datetime import datetime
    m = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() / 60
    return {"start": start, "end": end, "kind": kind, "minutes": round(m)}


# Real alerts of 6–8 October 2026 (Kyiv time).
OCT = [alert("2026-10-06T21:09", "2026-10-06T21:58"), alert("2026-10-06T22:59", "2026-10-06T23:25"),
       alert("2026-10-07T03:10", "2026-10-07T07:06", "missile"),
       alert("2026-10-07T09:48", "2026-10-07T10:07"), alert("2026-10-07T13:24", "2026-10-07T13:25"),
       alert("2026-10-07T15:29", "2026-10-07T15:46"), alert("2026-10-07T16:28", "2026-10-07T20:53"),
       alert("2026-10-07T21:29", "2026-10-07T22:36"), alert("2026-10-07T23:30", "2026-10-07T23:48", "missile"),
       alert("2026-10-07T23:49", "2026-10-08T00:22"), alert("2026-10-08T02:29", "2026-10-08T04:24")]


def by_id(alerts):
    return {a["id"]: a for a in attacks.segment(alerts)}


# --- segmentation -------------------------------------------------------------

def test_one_night_is_one_attack_even_with_a_lull():
    """21:09 drones and the 03:10 missiles are the night of 6–7 October."""
    a = by_id(OCT)["2026-10-06-N"]
    assert a["start"] == "2026-10-06T21:09" and a["end"] == "2026-10-07T07:06"
    assert a["title"] == "Night of 6–7 October"


def test_an_afternoon_alert_that_runs_past_midnight_is_a_night():
    a = by_id(OCT)["2026-10-07-N"]
    assert a["start"] == "2026-10-07T15:29" and a["end"] == "2026-10-08T04:24"


def test_daytime_attack_has_its_own_id_and_title():
    a = by_id(OCT)["2026-10-07-D"]
    assert a["title"] == "Daytime attack, 7 October"
    assert a["kind"] == "day"


def test_a_night_does_not_run_on_into_the_next_afternoon():
    """Alerts under two hours apart all day long must not turn one night into
    a day and a half."""
    al = [alert("2026-11-06T19:20", "2026-11-07T07:36", "missile"), alert("2026-11-07T08:07", "2026-11-07T08:20"),
          alert("2026-11-07T09:30", "2026-11-07T12:21"), alert("2026-11-07T13:13", "2026-11-07T16:14")]
    got = by_id(al)
    assert got["2026-11-06-N"]["end"] == "2026-11-07T08:20"
    assert got["2026-11-07-D"]["start"] == "2026-11-07T09:30"


def test_title_across_a_month_boundary():
    assert attacks.title("N", "2026-10-31") == "Night of 31 October – 1 November"


def test_ids_are_unique_over_the_real_period():
    import json
    from pathlib import Path
    reg = json.loads((Path(__file__).parent.parent / "data" / "out" / "registry.json").read_text(encoding="utf-8"))
    ids = [a["id"] for a in attacks.segment(reg["alerts"])]
    assert len(ids) == len(set(ids))


# --- placing claims ----------------------------------------------------------

def claim(posted, time=None, post="p"):
    return {"post_time": posted, "time": time, "post": post, "quote": "пошкоджено будинок"}


def where(c, text=""):
    return attacks.place(c, text, attacks.segment(OCT))[0]


def test_a_morning_summary_belongs_to_the_night_not_to_a_short_daytime_alert():
    """The 09:00 cut and a naive 'next alert starts a new window' rule both put
    this post on the wrong attack."""
    assert where(claim("2026-10-07 11:40"), "Внаслідок нічної атаки у Дніпровському районі") == "2026-10-06-N"


def test_a_stated_impact_time_decides():
    assert where(claim("2026-10-07 10:20", time="09:55"), "Падіння уламків") == "2026-10-07-D"


def test_a_post_during_a_daytime_alert_belongs_to_it():
    assert where(claim("2026-10-07 10:05"), "Падіння уламків у Голосіївському районі") == "2026-10-07-D"


def test_a_late_morning_report_without_clues_is_the_night_s():
    assert where(claim("2026-10-07 13:00"), "Рятувальники ліквідували пожежу") == "2026-10-06-N"


def test_a_named_date_decides():
    assert where(claim("2026-10-07 18:00"), "Наслідки атаки у ніч на 7 жовтня") == "2026-10-06-N"


def test_a_recap_of_several_attacks_is_set_aside():
    aid, why = attacks.place(claim("2026-10-08 12:00"), "Внаслідок атак 6 та 7 жовтня пошкоджено 30 будинків",
                             attacks.segment(OCT))
    assert aid is None and why == "recap of several attacks"


def test_a_post_with_no_alert_is_set_aside():
    aid, why = attacks.place(claim("2026-10-05 12:00"), "", attacks.segment(OCT))
    assert aid is None and why == "no alert"


def test_named_dates():
    assert attacks.named_dates("внаслідок атак 19 та 21 жовтня") == {"2026-10-19", "2026-10-21"}
    assert attacks.named_dates("overnight on Nov. 7") == {"2026-11-07"}
    assert attacks.named_dates("о 03:40 пошкоджено") == set()


def test_reading_days_cover_the_window():
    a = by_id(OCT)["2026-10-06-N"]
    assert attacks.reading_days(a) == ["2026-10-06", "2026-10-07"]


# --- validate ----------------------------------------------------------------

def site(**kw):
    base = {"claims": ["c1"], "object_en": "Apartment block", "summary_en": "Debris hit a building.",
            "district": None, "address": None, "target": "residential_highrise", "weapon": "drone", "time": None}
    return {**base, **kw}


def c(i, quote, weapon="drone"):
    return {"id": i, "quote": quote, "weapon": weapon}


def test_district_is_taken_from_the_quotes():
    v = validate.check([c("c1", "У Подільському районі пошкоджено будинок")], [site(district="Оболонський")])
    assert v["sites"][0]["district"] == "Подільський"


def test_a_bridge_name_is_not_a_district():
    assert validate.districts_named("пошкоджено Подільсько-Воскресенський мостовий перехід") == set()


def test_weapon_follows_most_reports():
    claims = [c("c1", "x", "ballistic"), c("c2", "y", "ballistic"), c("c3", "z", "drone")]
    v = validate.check(claims, [site(claims=["c1", "c2", "c3"], weapon="drone")])
    assert v["sites"][0]["weapon"] == "ballistic"


@pytest.mark.parametrize("summary", [
    "Another fire broke out in the yard.",
    "A second report of the same fire (c4).",
    "Debris fell (overnight) on a car.",
    "Kept separate per feedback from the auditor.",
    "Site 3 describes the same fire.",
])
def test_service_text_is_sent_back(summary):
    v = validate.check([c("c1", "x")], [site(summary_en=summary)])
    assert any(i["kind"] == "wording" for i in v["issues"])


@pytest.mark.parametrize("summary", [
    "A fire on the second floor of an apartment block.",
    "The building may have been hit by debris.",
])
def test_plain_wording_is_left_alone(summary):
    assert validate.check([c("c1", "x")], [site(summary_en=summary)])["issues"] == []


def test_dates_in_a_summary_are_sent_back():
    v = validate.check([c("c1", "x")], [site(summary_en="On 7 November debris hit a house.")])
    assert any("date" in i["explanation"] for i in v["issues"])


def test_one_bridge_on_two_places_is_a_missed_merge():
    sites = [site(object_en="Southern Bridge", target="transport"),
             site(object_en="Road surface", summary_en="The Southern Bridge deck was damaged.", target="transport")]
    v = validate.check([c("c1", "x")], sites)
    assert [i["sites"] for i in v["issues"] if i["kind"] == "missed_merge"] == [[1, 2]]


def test_two_different_bridges_are_not_merged():
    sites = [site(object_en="Southern Bridge"), site(object_en="Darnytskyi Bridge")]
    assert validate.check([c("c1", "x")], sites)["issues"] == []


def test_one_kindergarten_twice_is_flagged():
    sites = [site(object_en="Kindergarten"), site(object_en="Kindergarten building")]
    assert any(i["kind"] == "missed_merge" for i in validate.check([c("c1", "x")], sites)["issues"])


def test_title_is_built_by_code():
    assert validate.build_title(validate.clean_object("kindergarten", "education"), "Деснянський") == "Kindergarten – Desnianskyi"
    assert validate.clean_object("Site 3 duplicate (c4)", "vehicle") == "Vehicles"
    assert validate.clean_object("Будинок", "residential_house") == "House"


# --- auditor rounds and public notes ------------------------------------------

def test_second_revision_only_for_merge_errors():
    merge = {"verdict": "revise", "issues": [{"kind": "missed_merge"}]}
    field = {"verdict": "revise", "issues": [{"kind": "wrong_field"}]}
    assert _send_back(field, 0) and _send_back(merge, 0)
    assert _send_back(merge, 1) and not _send_back(field, 1)
    assert not _send_back(merge, 2)


@pytest.mark.parametrize("note", ["Site 2 and site 4 are the same (c3).", "Те саме місце.",
                                  "The matcher kept two claims apart.", "x" * 141, ""])
def test_unusable_public_note_falls_back_to_plain_words(note):
    out = public_note({"kind": "missed_merge", "public_note_en": note})
    assert out == "This may be the same strike as another record from this attack."


def test_good_public_note_is_kept():
    n = "May be the same fire as the warehouse record in Darnytskyi district."
    assert public_note({"kind": "missed_merge", "public_note_en": n}) == n


def test_public_summary_drops_pipeline_sentences():
    s = public_summary("A drone hit a warehouse. Claims c3 and c4 were kept apart.", "Warehouse.")
    assert s == "A drone hit a warehouse."
    assert not validate.public_text_problems(s)


@pytest.mark.parametrize("text, night", [
    ("Наслідки атаки у ніч на 7 листопада", "2026-11-06-N"),
    ("в ніч з 6 на 7 листопада", "2026-11-06-N"),
    ("у ніч на 1 листопада", "2026-10-31-N"),
    ("вранці 7 листопада", None),
])
def test_the_night_a_post_names(text, night):
    assert attacks.night_named(text) == night


def test_a_morning_recap_after_a_short_alert_follows_up_the_night():
    """8 October, 08:30: the city's recap of the night of 7–8 October went out
    the minute a 07:40 daytime alert ended. It repeats places already filed
    under the night, so it belongs to the night."""
    al = OCT + [alert("2026-10-08T07:40", "2026-10-08T08:30")]
    night = {"post": "a", "post_time": "2026-10-07 17:04", "time": None, "district": "Солом'янський",
             "target": "residential_highrise", "quote": "пожежа в 5-поверховому будинку"}
    recap = [{"post": "b", "post_time": "2026-10-08 08:30", "time": None, "district": d, "target": t,
              "quote": "Наслідки атаки"} for d, t in (("Солом'янський", "residential_highrise"),
                                                        ("Оболонський", "vehicle"))]
    obolon = {**night, "post": "c", "post_time": "2026-10-07 19:33", "district": "Оболонський", "target": "vehicle"}
    out = attacks.assign([night, obolon] + recap, {}, attacks.segment(al))["by_attack"]
    assert {c["post"] for c in out["2026-10-07-N"]} == {"a", "b", "c"}
    assert "2026-10-08-D" not in out


def test_a_new_place_after_a_short_alert_stays_with_that_alert():
    al = OCT + [alert("2026-10-08T07:40", "2026-10-08T08:30")]
    night = {"post": "a", "post_time": "2026-10-07 17:04", "time": None, "district": "Солом'янський",
             "target": "residential_highrise", "quote": "x"}
    new = {"post": "b", "post_time": "2026-10-08 08:20", "time": None, "district": "Печерський",
           "target": "warehouse", "quote": "уламки на склад"}
    out = attacks.assign([night, new], {}, attacks.segment(al))["by_attack"]
    assert [c["post"] for c in out["2026-10-08-D"]] == ["b"]


def test_a_night_s_own_reports_never_follow_up_the_day_before():
    """19–20 November: a Podilskyi fire at 02:50, during the night's alert,
    is the night's, even though the daytime attack also hit Podilskyi."""
    al = [alert("2026-11-19T11:34", "2026-11-19T17:37"), alert("2026-11-19T20:53", "2026-11-20T07:53", "missile")]
    day = {"post": "a", "post_time": "2026-11-19 13:05", "time": None, "district": "Подільський",
           "target": "other", "quote": "x"}
    night = {**day, "post": "b", "post_time": "2026-11-20 02:50"}
    out = attacks.assign([day, night], {}, attacks.segment(al))["by_attack"]
    assert [c["post"] for c in out["2026-11-19-N"]] == ["b"]


def test_a_quote_naming_two_districts_does_not_vote():
    """'В Подільському та Дарницькому районах' must not move a Podilskyi place to Darnytskyi."""
    assert validate.districts_named("В Подільському та Дарницькому районах пошкоджено") == {"Подільський", "Дарницький"}
    claims = [c("c1", "В Подільському та Дарницькому районах пошкоджено інфраструктуру"),
              c("c2", "У Дарницькому районі пошкоджено інфраструктуру")]
    v = validate.check(claims, [site(claims=["c1", "c2"], district="Подільський")])
    assert v["sites"][0]["district"] == "Подільський"


def test_bridges_named_after_districts_are_not_districts():
    assert validate.districts_named("пошкоджень зазнали Дарницький та Південний мости") == set()
    assert validate.districts_named("Подільсько-Воскресенський мостовий перехід") == set()
    assert validate.districts_named("на Дарницькому мості") == set()


def test_today_in_an_evening_post_is_the_daytime_attack():
    """19 November, 21:02: 'Сьогодні … постраждала будівля Володимирського собору',
    posted eight minutes into the night's first alert."""
    al = [alert("2026-11-19T11:34", "2026-11-19T17:37"), alert("2026-11-19T20:53", "2026-11-20T07:53", "missile")]
    c1 = {"post": "a", "post_time": "2026-11-19 21:02", "time": None, "district": "Шевченківський",
          "target": "cultural_religious", "quote": "постраждала будівля собору"}
    aid, why = attacks.place(c1, "Сьогодні внаслідок дронової атаки РФ по Києву постраждала будівля собору",
                             attacks.segment(al))
    assert (aid, why) == ("2026-11-19-D", "day words")
