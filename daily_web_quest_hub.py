# { "Depends": "py-genlayer:test" }

from genlayer import *
import json
import re
import typing

MONTHS = [
    "January","February","March","April","May","June",
    "July","August","September","October","November","December"
]

UTC_TIME_API = "https://worldtimeapi.org/api/timezone/Etc/UTC"

class DailyWebQuestHub(gl.Contract):
    # --- today ---
    day: str

    # --- Game 1: WikiWordle ---
    w_url: str
    w_answer: str
    w_clue: str
    w_excerpt: str

    # --- Game 2: ChronoGuess ---
    c_url: str
    c_year: u256
    c_clue: str
    c_excerpt: str
    c_anchor: str

    # --- stats ---
    total_actions: u256

    # --- per-user (storage collections; DO NOT init with TreeMap() in __init__) ---
    user_last_day: TreeMap[Address, str]

    w_attempts: TreeMap[Address, u256]
    w_solved: TreeMap[Address, bool]
    w_last_guess: TreeMap[Address, str]
    w_last_feedback: TreeMap[Address, str]

    c_attempts: TreeMap[Address, u256]
    c_solved: TreeMap[Address, bool]
    c_last_guess: TreeMap[Address, u256]
    c_last_hint: TreeMap[Address, str]

    points: TreeMap[Address, u256]

    def __init__(self):
        # primitives only
        self.day = ""
        self.w_url = ""
        self.w_answer = ""
        self.w_clue = "Press Sync to generate today's quests."
        self.w_excerpt = ""

        self.c_url = ""
        self.c_year = u256(0)
        self.c_clue = ""
        self.c_excerpt = ""
        self.c_anchor = ""

        self.total_actions = u256(0)

        # TreeMap fields are auto-empty; no manual init needed.

    # ---------------- helpers ----------------

    def _norm(self, s: str) -> str:
        return re.sub(r"\s+", " ", s).strip().lower()

    def _wiki_url_for_day(self, day: str) -> str:
        parts = day.split("-")
        month = int(parts[1])
        d = int(parts[2])
        return "https://en.wikipedia.org/wiki/" + MONTHS[month - 1] + "_" + str(d)

    def _reset_user_for_day(self, user: Address, day: str) -> None:
        self.user_last_day[user] = day

        self.w_attempts[user] = u256(0)
        self.w_solved[user] = False
        self.w_last_guess[user] = ""
        self.w_last_feedback[user] = ""

        self.c_attempts[user] = u256(0)
        self.c_solved[user] = False
        self.c_last_guess[user] = u256(0)
        self.c_last_hint[user] = ""

    def _ensure_user_day(self, user: Address) -> None:
        last = self.user_last_day.get(user, "")
        if last != self.day:
            self._reset_user_for_day(user, self.day)

    def _wordle_feedback(self, answer: str, guess: str) -> str:
        a = list(answer)
        g = list(guess)
        res = ["B","B","B","B","B"]
        used_a = [False]*5
        used_g = [False]*5

        for i in range(5):
            if g[i] == a[i]:
                res[i] = "G"
                used_a[i] = True
                used_g[i] = True

        for i in range(5):
            if used_g[i]:
                continue
            for j in range(5):
                if used_a[j]:
                    continue
                if g[i] == a[j]:
                    res[i] = "Y"
                    used_a[j] = True
                    break

        return "".join(res)

    # ---------------- public: sync ----------------

    @gl.public.write
    def sync_today(self) -> typing.Dict[str, typing.Any]:
        def fetch_utc_day() -> str:
            raw = gl.get_webpage(UTC_TIME_API, mode="text")
            data = json.loads(raw)
            dt = str(data.get("datetime", ""))
            return dt.split("T", 1)[0]

        today = gl.eq_principle_strict_eq(fetch_utc_day)

        if self.day == today and self.w_answer != "" and self.c_year != u256(0):
            return {"day": self.day, "url": self.w_url}

        url = self._wiki_url_for_day(today)

        def leader_fn() -> typing.Dict[str, typing.Any]:
            page = gl.get_webpage(url, mode="text")
            page_cut = page[:9000]

            prompt = f"""
Generate TWO daily games from Wikipedia text for: {url}

WORDLE:
- pick ONE word that appears in the text
- exactly 5 letters, lowercase a-z
- avoid very common words (about, there, which, their, other, first)
- clue: one short sentence grounded in the text, replace the word with "_____"
- excerpt: same sentence with the word visible

CHRONO:
- pick ONE entry that includes a 4-digit year from 1000..2099 that appears in the text
- chrono_clue: short grounded sentence, replace the year with "YYYY"
- chrono_excerpt: same sentence with the year visible
- anchor: 50-80 chars substring from chrono_clue (without YYYY) that appears verbatim in the page (case-insensitive)

Return ONLY JSON:
{{
 "wordle": {{"answer":"abcde","clue":"..._____...","excerpt":"...abcde..."}},
 "chrono": {{"year": 1999, "clue":"...YYYY...","excerpt":"...1999...","anchor":"..."}},
 "url": "{url}"
}}

Text:
{page_cut}
"""
            r = gl.nondet.exec_prompt(prompt, response_format="json")
            w = r.get("wordle", {})
            c = r.get("chrono", {})
            return {
                "url": str(r.get("url", url)),
                "wordle": {
                    "answer": str(w.get("answer", "")).strip().lower(),
                    "clue": str(w.get("clue", "")).strip(),
                    "excerpt": str(w.get("excerpt", "")).strip(),
                },
                "chrono": {
                    "year": c.get("year", 0),
                    "clue": str(c.get("clue", "")).strip(),
                    "excerpt": str(c.get("excerpt", "")).strip(),
                    "anchor": str(c.get("anchor", "")).strip(),
                }
            }

        def validator_fn(leader_res: gl.vm.Result) -> bool:
            if not isinstance(leader_res, gl.vm.Return):
                return False
            dat = leader_res.calldata

            if str(dat.get("url", "")).strip() != url:
                return False

            page = gl.get_webpage(url, mode="text")
            page_n = self._norm(page)

            # validate wordle
            w = dat.get("wordle", {})
            ans = str(w.get("answer", "")).strip().lower()
            clue = str(w.get("clue", "")).strip()
            ex = str(w.get("excerpt", "")).strip()

            if re.match(r"^[a-z]{5}$", ans) is None:
                return False
            if "_____" not in clue:
                return False
            if ans in clue.lower():
                return False
            if ans not in ex.lower():
                return False
            if ans not in page_n:
                return False

            # validate chrono
            c = dat.get("chrono", {})
            try:
                y_int = int(c.get("year", 0))
            except:
                return False

            if y_int < 1000 or y_int > 2099:
                return False

            c_clue = str(c.get("clue", "")).strip()
            c_ex = str(c.get("excerpt", "")).strip()
            anchor = str(c.get("anchor", "")).strip()

            if "YYYY" not in c_clue:
                return False
            if str(y_int) in c_clue:
                return False
            if str(y_int) not in c_ex:
                return False
            if str(y_int) not in page:
                return False
            if len(anchor) < 30:
                return False
            if self._norm(anchor) not in page_n:
                return False

            return True

        picked = gl.vm.run_nondet(leader_fn, validator_fn)

        self.day = today
        self.w_url = url
        self.w_answer = str(picked["wordle"]["answer"])
        self.w_clue = str(picked["wordle"]["clue"])
        self.w_excerpt = str(picked["wordle"]["excerpt"])

        self.c_url = url
        self.c_year = u256(int(picked["chrono"]["year"]))
        self.c_clue = str(picked["chrono"]["clue"])
        self.c_excerpt = str(picked["chrono"]["excerpt"])
        self.c_anchor = str(picked["chrono"]["anchor"])

        self.total_actions += u256(1)
        return {"day": self.day, "url": self.w_url}

    # ---------------- public: read puzzles ----------------

    @gl.public.view
    def get_wordle(self) -> typing.Dict[str, typing.Any]:
        return {"day": self.day, "clue": self.w_clue, "source_url": self.w_url}

    @gl.public.view
    def get_chrono(self) -> typing.Dict[str, typing.Any]:
        return {"day": self.day, "clue": self.c_clue, "source_url": self.c_url}

    # ---------------- public: play wordle ----------------

    @gl.public.write
    def submit_wordle(self, guess: str) -> typing.Dict[str, typing.Any]:
        user = gl.message.sender_address
        g = guess.strip().lower()

        if self.day == "" or self.w_answer == "":
            raise Exception("Not synced yet. Call sync_today() first.")
        if re.match(r"^[a-z]{5}$", g) is None:
            raise Exception("Guess must be exactly 5 letters a-z.")

        self._ensure_user_day(user)

        if self.w_solved.get(user, False):
            return {"status": "already_solved"}

        a = self.w_attempts.get(user, u256(0))
        if int(a) >= 6:
            return {"status": "no_attempts_left"}

        a = a + u256(1)
        self.w_attempts[user] = a
        self.w_last_guess[user] = g

        fb = self._wordle_feedback(self.w_answer, g)
        self.w_last_feedback[user] = fb

        self.total_actions += u256(1)

        if g == self.w_answer:
            self.w_solved[user] = True
            pts = u256(7 - int(a))
            self.points[user] = self.points.get(user, u256(0)) + pts
            return {"status": "solved", "attempt": int(a), "feedback": fb, "points_awarded": int(pts)}

        return {"status": "ok", "attempt": int(a), "feedback": fb}

    @gl.public.view
    def get_my_wordle(self, user_address: str) -> typing.Dict[str, typing.Any]:
        u = Address(user_address)
        return {
            "day": self.day,
            "attempts": int(self.w_attempts.get(u, u256(0))),
            "solved": bool(self.w_solved.get(u, False)),
            "last_guess": self.w_last_guess.get(u, ""),
            "last_feedback": self.w_last_feedback.get(u, ""),
            "points": int(self.points.get(u, u256(0))),
        }

    # ---------------- public: play chrono ----------------

    @gl.public.write
    def submit_chrono(self, year: u256) -> typing.Dict[str, typing.Any]:
        user = gl.message.sender_address
        if self.day == "" or self.c_year == u256(0):
            raise Exception("Not synced yet. Call sync_today() first.")

        y = int(year)
        if y < 1000 or y > 2099:
            raise Exception("Year must be 1000..2099.")

        self._ensure_user_day(user)

        if self.c_solved.get(user, False):
            return {"status": "already_solved"}

        a = self.c_attempts.get(user, u256(0))
        if int(a) >= 5:
            return {"status": "no_attempts_left"}

        a = a + u256(1)
        self.c_attempts[user] = a
        self.c_last_guess[user] = u256(y)

        target = int(self.c_year)
        if y == target:
            self.c_solved[user] = True
            hint = "correct (+0)"
            self.c_last_hint[user] = hint
            pts = u256(6 - int(a))
            self.points[user] = self.points.get(user, u256(0)) + pts
            self.total_actions += u256(1)
            return {"status": "solved", "attempt": int(a), "hint": hint, "points_awarded": int(pts)}

        diff = y - target
        hint = ("low (+" + str(-diff) + ")") if diff < 0 else ("high (+" + str(diff) + ")")
        self.c_last_hint[user] = hint
        self.total_actions += u256(1)
        return {"status": "ok", "attempt": int(a), "hint": hint}

    @gl.public.view
    def get_my_chrono(self, user_address: str) -> typing.Dict[str, typing.Any]:
        u = Address(user_address)
        return {
            "day": self.day,
            "attempts": int(self.c_attempts.get(u, u256(0))),
            "solved": bool(self.c_solved.get(u, False)),
            "last_guess": int(self.c_last_guess.get(u, u256(0))),
            "last_hint": self.c_last_hint.get(u, ""),
            "points": int(self.points.get(u, u256(0))),
        }

    @gl.public.view
    def reveal(self) -> typing.Dict[str, typing.Any]:
        return {
            "day": self.day,
            "wordle": {"answer": self.w_answer, "excerpt": self.w_excerpt, "url": self.w_url},
            "chrono": {"year": int(self.c_year), "excerpt": self.c_excerpt, "url": self.c_url},
            "total_actions": int(self.total_actions),
        }
