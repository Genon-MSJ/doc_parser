"""제목-only 청크 흡수(_merge_heading_only_chunks)가 어떤 설정에서도 무손실인지 검증.

배경: md 의 `#` 는 TITLE 아이템이 되고, 바로 뒤에 `##` 가 오면 TITLE 하나짜리 섹션이 된다.
30자를 넘는 제목은 3단계 단독 타이틀 병합에서 제외되므로 단독 청크로 남고, _merge_heading_only_chunks
가 이를 다음 청크로 흡수한다. 이때 제목 텍스트는 HEADER 라인(=headings 경로)으로만 렌더되므로
include_chunk_header 가 꺼져 있으면 제목이 산출물에서 완전히 사라졌다
(실측: 상품설명서 생명 md 의 40자 H1 이 청크 어디에도 없었다).
"""

import pytest

pytestmark = pytest.mark.unit

from docling_core.transforms.chunker import DocChunk, DocMeta
from docling_core.types import DoclingDocument
from docling_core.types.doc.labels import DocItemLabel

from genon.preprocessor.processing.chunking import chunk_quality as cq
from genon.preprocessor.processing.chunking.smart_chunker import SmartChunkerBase

# 3단계 병합 임계(30자)를 넘는 제목. 실제 문제 문서의 H1 과 같은 길이대다.
_LONG_TITLE = "삼성 1540 청춘대표·4180 인생대표·팩 건강보험(2607) 상품요약서"
_SHORT_TITLE = "문서 개요"
_BODY = "피보험자 가입나이 만 15세 이상 40세 이하"


def _chunker(include_chunk_header: bool) -> SmartChunkerBase:
    """검증 대상 메서드만 쓰는 최소 인스턴스. max_tokens=0 이면 _fits 가 토크나이저를 타지 않는다."""
    return SmartChunkerBase.model_construct(
        include_chunk_header=include_chunk_header, max_tokens=0)


def _chunks(title: str):
    """제목-only 청크 + 본문 청크. doc_items 는 실제 DocItem 이어야 유형 판정이 동작한다."""
    doc = DoclingDocument(name="sample")
    title_item = doc.add_title(text=title)
    body_item = doc.add_text(label=DocItemLabel.TEXT, text=_BODY)
    return [
        DocChunk(text=title, meta=DocMeta(doc_items=[title_item], headings=[title])),
        DocChunk(text=_BODY, meta=DocMeta(doc_items=[body_item], headings=[title])),
    ]


def test_header_off_keeps_title_in_body():
    """헤더 라인이 꺼져 있으면 제목을 본문에 이어붙여 유실을 막는다."""
    result = _chunker(include_chunk_header=False)._merge_heading_only_chunks(_chunks(_LONG_TITLE))

    assert len(result) == 1
    assert _LONG_TITLE in result[0].text
    assert _BODY in result[0].text


def test_header_on_keeps_body_free_of_title():
    """헤더 라인이 켜져 있으면 제목은 경로로만 렌더되므로 본문에 중복시키지 않는다."""
    result = _chunker(include_chunk_header=True)._merge_heading_only_chunks(_chunks(_LONG_TITLE))

    assert len(result) == 1
    assert result[0].text == _BODY
    assert _LONG_TITLE in result[0].meta.headings


def test_short_title_also_absorbed_losslessly():
    """30자 이하 제목도 같은 경로를 탄다(3단계에서 병합되지 못하고 남은 경우)."""
    result = _chunker(include_chunk_header=False)._merge_heading_only_chunks(_chunks(_SHORT_TITLE))

    assert len(result) == 1
    assert _SHORT_TITLE in result[0].text


def test_trailing_title_merges_backward_losslessly():
    """문서 끝에 남은 제목-only 청크는 이전 청크로 후방 병합되며 텍스트가 보존된다."""
    body_chunk, title_chunk = _chunks(_LONG_TITLE)[1], _chunks(_LONG_TITLE)[0]
    result = _chunker(include_chunk_header=False)._merge_heading_only_chunks(
        [body_chunk, title_chunk])

    assert len(result) == 1
    assert _LONG_TITLE in result[0].text
    assert result[0].text.index(_BODY) < result[0].text.index(_LONG_TITLE)


# --- 짧은 본문 청크 병합(short_chunk_size) ---
# 상품설명서는 본문이 1~2줄뿐인 섹션이 많아 HEADER 경로가 대부분인 청크가 생긴다. 장이 바뀌어도
# 다음 청크에 붙이고, 끄면(0) 지금과 같아야 한다.
_SHORT_BODY = "벌금은 1사고당 2천만원 한도로 보상합니다."


def _short_then_next(next_is_table: bool = False):
    """짧은 본문 청크(제1장) + 다음 청크(제2장). next_is_table 이면 다음 청크가 표다."""
    doc = DoclingDocument(name="sample")
    short_item = doc.add_text(label=DocItemLabel.TEXT, text=_SHORT_BODY)
    if next_is_table:
        from docling_core.types.doc.document import TableData
        next_item = doc.add_table(data=TableData(num_rows=1, num_cols=1))
    else:
        next_item = doc.add_text(label=DocItemLabel.TEXT, text=_BODY)
    return [
        DocChunk(text=_SHORT_BODY, meta=DocMeta(doc_items=[short_item], headings=["제1장 > 2. 벌금"])),
        DocChunk(text=_BODY, meta=DocMeta(doc_items=[next_item], headings=["제2장 > 1. 보장"])),
    ]


@pytest.mark.parametrize("short_chunk_size, merged", [(0, False), (200, True)])
def test_short_body_merges_into_next_chunk_across_chapters(short_chunk_size, merged):
    chunker = SmartChunkerBase.model_construct(
        include_chunk_header=True, max_tokens=0, short_chunk_size=short_chunk_size)
    result = chunker._merge_heading_only_chunks(_short_then_next())

    if not merged:
        assert [c.text for c in result] == [_SHORT_BODY, _BODY]
        return
    assert len(result) == 1
    assert result[0].text == f"{_SHORT_BODY}\n{_BODY}"
    assert result[0].meta.headings == ["제1장 > 2. 벌금", "제2장 > 1. 보장"]


def _text_chunk(doc, text, heading):
    item = doc.add_text(label=DocItemLabel.TEXT, text=text)
    return DocChunk(text=text, meta=DocMeta(doc_items=[item], headings=[heading]))


def test_no_short_chunk_survives_even_over_size_limit():
    """연속한 짧은 청크는 크기 한도를 넘어도 모두 뒤 청크에, 뒤가 없는 것만 앞 청크에 붙는다."""
    doc = DoclingDocument(name="sample")
    chunks = [_text_chunk(doc, "보험기간은 1년입니다.", "가"),
              _text_chunk(doc, "납입은 일시납입니다.", "나"),
              _text_chunk(doc, _BODY, "다"),
              _text_chunk(doc, "문의 1588-3114", "라")]
    chunker = SmartChunkerBase.model_construct(
        include_chunk_header=True, max_tokens=len(_BODY), short_chunk_size=200)
    result = chunker._merge_heading_only_chunks(chunks)

    assert [c.text for c in result] == [
        f"보험기간은 1년입니다.\n납입은 일시납입니다.\n{_BODY}\n문의 1588-3114"]
    assert result[0].meta.headings == ["가", "나", "다", "라"]


def test_short_body_merges_into_table_chunk():
    """table_as_chunk 의 표 청크도 짧은 청크를 받는다(짧은 청크를 남기지 않는 것이 우선)."""
    chunker = SmartChunkerBase.model_construct(
        include_chunk_header=True, max_tokens=0, short_chunk_size=200, table_as_chunk=True)
    result = chunker._merge_heading_only_chunks(_short_then_next(next_is_table=True))

    assert [c.text for c in result] == [f"{_SHORT_BODY}\n{_BODY}"]


# --- 실제 검증 판정(chunk_quality)과 함께 쓸 때 ---
_DROP = cq.rejects_doc_chunk(cq.Config(action="drop"))


def _force(**kwargs):
    return SmartChunkerBase.model_construct(
        include_chunk_header=True, max_tokens=0, short_chunk_size=200, **kwargs)


def test_merge_never_creates_a_chunk_the_validation_drops():
    """하나하나는 정상인 같은 안내 문구 10개를 합치면 반복으로 판정되어 문서 전체가 실패했다.
    합친 결과가 버려질 청크가 되는 병합은 하지 않는다."""
    doc = DoclingDocument(name="sample")
    chunks = [_text_chunk(doc, "해당 특약은 가입 시 선택할 수 있습니다.", f"특약{i}") for i in range(10)]
    result = _force(merge_guard=_DROP)._merge_heading_only_chunks(chunks)

    assert not any(_DROP(c) for c in result)
    assert "\n".join(c.text for c in result).count("해당 특약은") == 10


def test_formula_chunk_kept_by_validation_is_not_skipped():
    """검증은 수식 청크를 길이 하한에서 예외로 둔다. 같은 판정을 쓰지 않으면 살아남는 수식 청크를
    건너뛰어 [가, 수식, 다] 가 [가+다, 수식] 으로 뒤집혔다."""
    doc = DoclingDocument(name="sample")
    formula = doc.add_text(label=DocItemLabel.FORMULA, text="x=1")
    chunks = [_text_chunk(doc, "납입 조건은 아래 수식을 적용합니다.", "가"),
              DocChunk(text="x=1", meta=DocMeta(doc_items=[formula], headings=["나"])),
              _text_chunk(doc, "일반 설명이 담긴 다음 문단입니다.", "다")]
    result = _force(merge_guard=_DROP)._merge_heading_only_chunks(chunks)

    text = "\n".join(c.text for c in result)
    assert text.index("납입 조건") < text.index("x=1") < text.index("일반 설명")


def test_heading_only_titles_survive_header_leaf_limit():
    """HEADER 는 경로를 5개까지만 렌더한다. 제목을 경로로만 남기면 6번째부터 산출물에서 사라졌다."""
    doc = DoclingDocument(name="sample")
    titles = [f"독립항목{i}" for i in range(8)]
    chunks = [DocChunk(text=t, meta=DocMeta(doc_items=[doc.add_title(text=t)], headings=[t]))
              for t in titles] + [_text_chunk(doc, _BODY, "본문")]
    result = _force()._merge_heading_only_chunks(chunks)

    assert len(result) == 1
    assert all(t in result[0].text for t in titles)
