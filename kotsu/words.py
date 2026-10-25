"""Craft-word lexicon and parsing of what the master says.

The master's speech arrives as text (from the browser's Japanese speech
recognition, or from a word button). This module turns that text into:
  * a canonical craft word (グッ, スーッ, ...), possibly a brand-new one,
  * an intent: correction ("ちがう、もっとグッと"), demonstration ("グッと"),
    or approval ("そう！", "いいね").
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Canonical craft words with spoken variants (after kana normalisation to katakana).
# gloss_en is a short, human gloss shown to learners next to the word (a hint, not the meaning).
LEXICON: dict[str, dict] = {
    "スーッ": {"variants": ["スーッ", "スー", "スウッ", "スーット", "スゥーッ"], "romaji": "sūtto", "gloss_en": "one smooth, even glide"},
    "スッ": {"variants": ["スッ", "スット"], "romaji": "sutto", "gloss_en": "quick and light"},
    "グッ": {"variants": ["グッ", "グット", "グー"], "romaji": "gutto", "gloss_en": "one firm press"},
    "グイッ": {"variants": ["グイッ", "グイット", "グイ"], "romaji": "guitto", "gloss_en": "a strong, sudden pull"},
    "ピタッ": {"variants": ["ピタッ", "ピタット", "ピタリ", "ピッタリ"], "romaji": "pitatto", "gloss_en": "a dead stop, exactly there"},
    "ジワッ": {"variants": ["ジワッ", "ジワット", "ジワーッ", "ジワーット", "ジワジワ", "ジワ", "ジワリ"], "romaji": "jiwatto", "gloss_en": "slowly building up"},
    "ギュッ": {"variants": ["ギュッ", "ギュット", "ギュー"], "romaji": "gyutto", "gloss_en": "squeeze and hold"},
    "トン": {"variants": ["トン", "トンッ"], "romaji": "ton", "gloss_en": "one light tap"},
    "トントン": {"variants": ["トントン"], "romaji": "tonton", "gloss_en": "light taps in rhythm"},
    "ガタガタ": {"variants": ["ガタガタ"], "romaji": "gatagata", "gloss_en": "shaky and jerky (a fault)", "fault": True},
    "ガリガリ": {"variants": ["ガリガリ"], "romaji": "garigari", "gloss_en": "rough, grinding (a fault)", "fault": True},
    "シャーッ": {"variants": ["シャーッ", "シャーット", "シャー"], "romaji": "shātto", "gloss_en": "a clean, hissing stroke"},
    "サッ": {"variants": ["サッ", "サット"], "romaji": "satto", "gloss_en": "swift and clean"},
    "フワッ": {"variants": ["フワッ", "フワット", "フワリ"], "romaji": "fuwatto", "gloss_en": "soft and light"},
    "カチッ": {"variants": ["カチッ", "カチット"], "romaji": "kachitto", "gloss_en": "until it clicks"},
}

CORRECTION_CUES = ["チガウ", "違う", "モット", "マダマダ", "ダメ", "駄目", "ソウジャナイ", "イイエ", "ヨワイ", "ツヨイ", "ハヤイ", "オソイ", "モウスコシ", "モウチョット"]
APPROVAL_CUES = ["ソウソウ", "ソウ", "イイネ", "イイ", "ヨシ", "OK", "オッケー", "ジョウズ", "上手", "カンペキ", "完璧", "バッチリ"]


def to_katakana(text: str) -> str:
    """Normalise width and convert hiragana to katakana."""
    text = unicodedata.normalize("NFKC", text)
    out = []
    for ch in text:
        code = ord(ch)
        if 0x3041 <= code <= 0x3096:  # hiragana block
            out.append(chr(code + 0x60))
        else:
            out.append(ch)
    return "".join(out)


def _squash(text: str) -> str:
    text = to_katakana(text).upper()
    text = re.sub(r"[\s、。,.!！?？「」『』・〜~]", "", text)
    return text


@dataclass
class Parsed:
    word: str | None          # canonical craft word, or None
    intent: str               # "correction" | "demonstration" | "approval" | "bad_example" | "none"
    is_new_word: bool         # True when the word was not in the lexicon
    text: str                 # original text
    fault: str | None = None  # a fault word that was mentioned (ガタガタ), if any


_MORA = "[ァ-ヴ][ァィゥェォャュョヮ]?"
_SHAPES = [re.compile(rf"^(?:{_MORA}){{1,2}}ッ$"),            # グッ ズバッ ギュッ
           re.compile(rf"^((?:{_MORA}){{1,2}})\1$"),           # ガタガタ ジワジワ
           re.compile(rf"^(?:{_MORA}){{1,2}}ー+ッ?$")]          # スーッ ジワーッ シャーッ
_IN_TEXT = [re.compile(rf"((?:{_MORA}){{1,2}}ッ)(?=ト|$)"),
            re.compile(rf"((?:{_MORA}){{1,2}})\1"),
            re.compile(rf"((?:{_MORA}){{1,2}}ー+ッ?)(?=ト|$)")]


def _kata_keep(text: str) -> tuple[str, str]:
    """The text after width normalisation, and the same text in katakana (same length, punctuation kept)."""
    orig = unicodedata.normalize("NFKC", text)
    kata = "".join(chr(ord(c) + 0x60) if 0x3041 <= ord(c) <= 0x3096 else c for c in orig)
    return orig, kata.upper() if len(kata.upper()) == len(kata) else kata


_KATA = re.compile(r"[ァ-ヴー]")


def _cue_free(text: str) -> str:
    rest = _squash(text)
    for w in sorted(NOT_MIMETIC + CORRECTION_CUES + APPROVAL_CUES + BAD_EXAMPLE_CUES, key=len, reverse=True):
        rest = rest.replace(_squash(w), " ")
    return rest


def detect_new_mimetic(text: str) -> str | None:
    """Spot a mimetic-looking word that is not in the lexicon.

    Japanese mimetics typically end in a small ッ (グッ, ズバッ), double a
    one- or two-mora root (ガタガタ), or carry a long vowel (スーッ, ジワーッ).
    Katakana in the recognised text marks word edges, so whole katakana words
    are judged first (loanwords such as マスター or グッド do not fit the
    shapes); hiragana text falls back to a search that needs the word to be
    followed by と or to end the phrase.
    """
    orig = unicodedata.normalize("NFKC", text)
    for run in re.findall(r"[ァ-ヴー]+", orig):
        cand = run
        if cand.endswith("ト") and len(cand) >= 3 and any(p.match(cand[:-1]) for p in _SHAPES):
            cand = cand[:-1]
        if cand in NOT_MIMETIC or cand in LEXICON:
            continue
        if any(p.match(cand) for p in _SHAPES):
            return cand
    rest = _cue_free(re.sub(r"[ァ-ヴー]+", "|", orig))
    for pat in _IN_TEXT:
        for m in pat.finditer(rest):
            token = m.group(0)
            if len(token) >= 2 and token not in NOT_MIMETIC:
                return token
    return None


NOT_MIMETIC = ["モット", "チョット", "ヤッパリ", "ソウソウ", "ソウ", "イイ", "ヨシ", "オッケー", "チガウ", "ダメ", "ジョウズ",
               "モウスコシ", "モウチョット", "イッテ", "ヤッテ", "ミテ", "キッテ", "トッテ", "モッテ", "ハッキリ", "シッカリ",
               "ウン", "ハイ", "イイエ", "キット", "ズット", "ヤット", "ホット", "サッキ", "イロイロ", "マダマダ", "トキドキ",
               "ダンダン", "ナカナカ", "ハイハイ", "ウンウン", "イヤイヤ", "マスマス", "ワザワザ", "ネエネエ", "ホラホラ", "ドウゾ"]


def is_fault(word: str | None) -> bool:
    return bool(word and LEXICON.get(word, {}).get("fault"))


def lexicon_hits(text: str) -> list[tuple[int, int, str]]:
    """(position, length, canonical word) for every lexicon word in the text, longest match per place.

    A match written in katakana that runs on into more katakana is part of a
    longer word and does not count: グッド (good), スープ, サッカー, トンネル.
    """
    orig, kata = _kata_keep(text)
    hits: dict[int, tuple[int, str]] = {}
    for canon, info in LEXICON.items():
        for v in info["variants"]:
            start = kata.find(v)
            while start >= 0:
                end = start + len(v)
                runs_on = end < len(orig) and _KATA.match(orig[end]) and _KATA.match(orig[end - 1])
                runs_in = start > 0 and _KATA.match(orig[start - 1]) and _KATA.match(orig[start])
                if not runs_on and not runs_in and (start not in hits or len(v) > hits[start][0]):
                    hits[start] = (len(v), canon)
                start = kata.find(v, start + 1)
    out = []
    covered_until = -1
    for pos in sorted(hits):
        ln, canon = hits[pos]
        if pos < covered_until:
            continue  # inside a longer word (トン inside トントン)
        out.append((pos, ln, canon))
        covered_until = pos + ln
    return out


def canonical_word(text: str) -> tuple[str | None, bool]:
    hits = lexicon_hits(text)
    if hits:
        # The target of a correction is what follows もっと; fault words (ガタガタ) describe the error, not the goal.
        good = [h for h in hits if not is_fault(h[2])] or hits
        mo = _kata_keep(text)[1].find("モット")
        if mo >= 0:
            after = [h for h in good if h[0] >= mo]
            if after:
                return after[0][2], False
        return max(good, key=lambda h: (h[1], -h[0]))[2], False
    new = detect_new_mimetic(text)
    if new:
        return new, True
    return None, False


BAD_EXAMPLE_CUES = ["ダメナレイ", "駄目ナ例", "ダメナ例", "ワルイレイ", "悪イ例", "悪い例", "コウスルトダメ", "失敗例", "シッパイレイ"]


def parse_utterance(text: str) -> Parsed:
    sq = _squash(text)
    word, is_new = canonical_word(text)
    fault = next((h[2] for h in lexicon_hits(text) if is_fault(h[2])), None)
    if any(_squash(c) in sq for c in BAD_EXAMPLE_CUES):
        return Parsed(word=word, intent="bad_example", is_new_word=is_new, text=text, fault=fault)
    has_correction = any(_squash(c) in sq for c in CORRECTION_CUES)
    has_approval = (not has_correction) and any(sq.startswith(_squash(c)) for c in APPROVAL_CUES)
    if has_correction:
        intent = "correction"          # word may be None: then it applies to the current word
    elif has_approval:
        intent = "approval"
    elif word:
        intent = "demonstration"
    else:
        intent = "none"
    return Parsed(word=word, intent=intent, is_new_word=is_new, text=text, fault=fault)


def word_info(word: str) -> dict:
    info = LEXICON.get(word)
    if info:
        return {"word": word, "romaji": info["romaji"], "gloss_en": info["gloss_en"], "known": True}
    return {"word": word, "romaji": "", "gloss_en": "a new word from this master", "known": False}
