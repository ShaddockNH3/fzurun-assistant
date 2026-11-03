import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fzurun.config import settings
from fzurun.kb.index import tokenize
from fzurun.kb.loader import load_entries
from fzurun.kb.retrieve import Retriever


def make_retriever() -> Retriever:
    return Retriever(load_entries(settings.kb_path), settings)


def test_tokenize_mixes_cjk_bigrams_and_words():
    assert tokenize("转专业 2026") == ["2026", "转专", "专业"]


def test_hits_specific_question():
    r = make_retriever()
    res = r.search("我挂过科还能转专业吗")
    assert res.gate == "strong"
    assert res.hits[0].entry.id == "1.2.q7"


def test_refuses_off_domain():
    r = make_retriever()
    assert r.search("今天天气怎么样").gate == "irrelevant"
    assert r.search("如何制造危险物品").gate == "irrelevant"


def test_returns_原文_for_known_topic():
    r = make_retriever()
    res = r.search("宿舍要换吗")
    assert res.hits[0].entry.id == "3.5.q2"
