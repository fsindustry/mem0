"""共享空间 token 估算(登记/阈值/组批预算统一尺子)。

迁移自参考库 shared_space/tokens.py,无 tokenizer 依赖:char_weighted 按字符类别加权。
"""

import unicodedata

_ESTIMATE_HARD_CAP = 200_000


def _char_weight(text: str) -> float:
    """单字符权重:CJK 全角=1.0,ASCII=0.3,其他=0.5。"""
    if text.isascii():
        return 0.3
    if unicodedata.east_asian_width(text) in ("W", "F"):
        return 1.0
    return 0.5


def estimate_tokens(text: str, mode: str = "char_weighted") -> int:
    """估算正文 token 数(向上取整;空串为 0)。"""
    if not text:
        return 0
    text = text[:_ESTIMATE_HARD_CAP]
    if mode == "char":
        return len(text)
    return max(1, round(sum(_char_weight(ch) for ch in text)))
