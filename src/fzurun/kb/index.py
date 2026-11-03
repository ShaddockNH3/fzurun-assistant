"""中文友好的 BM25。

不引第三方分词器：中文按二元组切，英文数字按词切。
99 条语料的规模下，二元组的效果跟 jieba 差不多，但省一个依赖、也省一个词典加载。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

CJK = r"\u4e00-\u9fff"
RE_CJK_RUN = re.compile(rf"[{CJK}]+")
RE_WORD = re.compile(r"[a-z0-9]+")

# 疑问词/虚词二元组。它们在 99 篇里占了 20% 以上，按 df 比例拦不住，
# 但本身不携带主题，留着会把「降转是什么」推到「拟录取是什么意思」那条上去。
STOP = {
    "什么", "是什", "么意", "怎么", "么样", "怎样", "如何", "为什",
    "哪些", "哪个", "多少", "可以", "能不", "是否", "一下", "这个",
    "那个", "我们", "你们", "他们", "的话", "请问", "知道",
}


def tokenize(text: str, keep_stop: bool = False) -> list[str]:
    text = text.lower()
    tokens = RE_WORD.findall(text)
    for run in RE_CJK_RUN.findall(text):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens if keep_stop else [t for t in tokens if t not in STOP]


@dataclass
class Doc:
    id: str
    text: str
    tf: Counter = field(default_factory=Counter)
    length: int = 0


class BM25:
    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.docs: list[Doc] = []
        self.df: Counter = Counter()
        # 原始词表（含虚词），只用来判断「这个词语料里有没有」
        self.raw_vocab: set[str] = set()
        self.avg_len: float = 0.0
        self._n: int = 0

    def fit(self, items: list[tuple[str, str]]) -> None:
        """items: [(id, text)]"""
        self.docs = []
        self.df = Counter()
        self.raw_vocab = set()
        for _id, text in items:
            self.raw_vocab.update(tokenize(text, keep_stop=True))
            tf = Counter(tokenize(text))
            self.docs.append(Doc(id=_id, text=text, tf=tf, length=sum(tf.values())))
            for term in tf:
                self.df[term] += 1
        self._n = len(self.docs)
        self.avg_len = (sum(d.length for d in self.docs) / self._n) if self._n else 0.0

    def _idf(self, term: str) -> float:
        n = self._n
        df = self.df.get(term, 0)
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def scores(self, query: str) -> list[float]:
        q_terms = tokenize(query)
        if not q_terms or not self.docs:
            return [0.0] * len(self.docs)
        out = []
        for d in self.docs:
            s = 0.0
            norm = self.k1 * (1 - self.b + self.b * d.length / (self.avg_len or 1))
            for t in q_terms:
                f = d.tf.get(t, 0)
                if f:
                    s += self._idf(t) * f * (self.k1 + 1) / (f + norm)
            out.append(s)
        return out

    def vocab_ratio(self, query: str) -> float:
        """query 的 token 有多大比例真的出现在语料词表里。

        中文二元组会在词边界产生「转是」「试要」这类垃圾，它们 df=0，
        意味着这个词语料里根本没有——这恰恰是判断「域外问题」最直接的证据。
        """
        # 这里要用原始 token：虚词同样是「这个领域里有没有这个词」的证据，
        # 而词边界产生的垃圾二元组（「转是」「试要」）才是真正该计入分母的噪声。
        ts = tokenize(query, keep_stop=True)
        if not ts:
            return 0.0
        return sum(1 for t in ts if t in self.raw_vocab) / len(ts)

    def coverage(self, query: str, doc_index: int) -> float:
        """query 里有信息量的 token，有多大比例出现在这条文档里。

        只统计 df 低于 25% 语料的词：「怎么」「什么」「可以」这类几乎每篇都有，
        算进来会把「今天天气怎么样」抬到 weak，误判成能答。
        """
        q = set(tokenize(query))
        if not q:
            return 0.0
        ceiling = self._n * 0.25
        informative = {t for t in q if 0 < self.df.get(t, 0) <= ceiling}
        if not informative:
            return 0.0
        total = sum(self._idf(t) for t in informative)
        if total <= 0:
            return 0.0
        hit = informative & set(self.docs[doc_index].tf)
        return sum(self._idf(t) for t in hit) / total
