"""HTML 표의 <caption> 이 docling 문서에 남는지 검증한다.

이 처리가 없으면 캡션 텍스트가 표에도 본문에도 남지 않고 통째로 사라져,
청크만 보고는 그 표가 무엇에 대한 것인지 알 수 없다.
"""

import pytest


def _convert(tmp_path, body: str):
    pytest.importorskip("docling.document_converter", exc_type=ImportError)
    from docling.document_converter import DocumentConverter

    path = tmp_path / "table.html"
    path.write_text(f"<!doctype html><html><body>{body}</body></html>", encoding="utf-8")
    return DocumentConverter().convert(path).document


@pytest.mark.unit
def test_table_caption_survives_conversion(tmp_path):
    doc = _convert(tmp_path, """
        <table>
          <caption>2026년 월별 이용 내역</caption>
          <thead><tr><th>월</th><th>금액</th></tr></thead>
          <tbody><tr><td>1월</td><td>1000</td></tr></tbody>
        </table>""")

    assert doc.tables[0].caption_text(doc).strip() == "2026년 월별 이용 내역"
    assert "2026년 월별 이용 내역" in doc.export_to_markdown()


@pytest.mark.unit
def test_caption_text_is_not_mistaken_for_a_table_cell(tmp_path):
    doc = _convert(tmp_path, """
        <table>
          <caption>안내</caption>
          <tbody><tr><td>값</td></tr></tbody>
        </table>""")

    cells = [c.text for row in doc.tables[0].data.grid for c in row]
    assert cells == ["값"]


@pytest.mark.unit
@pytest.mark.parametrize("body", [
    "<table><tbody><tr><td>a</td></tr></tbody></table>",              # 캡션 없음
    "<table><caption>   </caption><tbody><tr><td>a</td></tr></tbody></table>",  # 공백뿐
])
def test_missing_or_blank_caption_leaves_table_uncaptioned(tmp_path, body):
    doc = _convert(tmp_path, body)
    assert doc.tables[0].caption_text(doc) == ""


@pytest.mark.unit
def test_inline_markup_inside_caption_is_flattened(tmp_path):
    doc = _convert(tmp_path, """
        <table><caption>월별 <b>이용</b> 내역</caption>
        <tbody><tr><td>a</td></tr></tbody></table>""")
    assert doc.tables[0].caption_text(doc).strip() == "월별 이용 내역"


@pytest.mark.unit
def test_nested_table_keeps_its_own_caption(tmp_path):
    """직계 자식만 봐야 바깥 표가 안쪽 표의 캡션을 가져가지 않는다."""
    doc = _convert(tmp_path, """
        <table><caption>바깥표</caption><tbody><tr><td>
          <table><caption>안쪽표</caption><tbody><tr><td>x</td></tr></tbody></table>
        </td></tr></tbody></table>""")

    captions = {t.caption_text(doc).strip() for t in doc.tables}
    assert captions == {"바깥표", "안쪽표"}


@pytest.mark.unit
@pytest.mark.parametrize("cell", [
    "월 최대 25,000원",                                             # 워드프레스 wp-block-table
    '<figure class="image"><img src="card.png" alt="카드"></figure>월 최대 25,000원',  # 셀 안 이미지
])
def test_table_wrapped_in_figure_survives_conversion(tmp_path, cell):
    """figure 분기가 첫 img 만 내보내면 figure 로 감싼 표가 본문에서 통째로 사라진다(#564)."""
    doc = _convert(tmp_path, f"""
        <figure class="wp-block-table"><table>
          <thead><tr><th>전월실적</th><th>혜택</th></tr></thead>
          <tbody><tr><td>30만원</td><td>{cell}</td></tr></tbody>
        </table></figure>""")

    assert len(doc.tables) == 1
    assert "월 최대 25,000원" in doc.export_to_markdown()


@pytest.mark.unit
def test_image_only_figure_keeps_picture_and_caption(tmp_path):
    doc = _convert(tmp_path, '<figure><img src="duck.png" alt="오리"><figcaption>오리 사진</figcaption></figure>')

    assert (len(doc.pictures), len(doc.tables)) == (1, 0)
    assert doc.pictures[0].caption_text(doc).strip() == "오리 사진"


# 실물 발췌: 서울시설공단 개인정보처리방침(https://sisul.or.kr/open_content/main/guidance/pim.jsp)
# 제5조 열람 양식 표(<ul> 의 직계 자식 <table>, 원문 그대로)와 제13조 영향평가 표(<li> 안 <table>).
_SISUL_UL_DIRECT_TABLE = """<ul>
                        <li class="ssblet">공단은 정보주체 권리에 따른 열람의 요구, 정정·삭제의 요구, 처리정지의 요구 시 열람 등 요구를 한 자가 본인이거나 정당한 대리인 인지를
                            확인합니다.</li>
                        <table class="data" summary="정보주체와 법정대리인의 권리,의무 행사 방법을 위한 양식을 문서명과 첨부파일로 나누어 정리한 표입니다." width="100%">
					        <caption>정보주체와 법정대리인의 권리,의무 행사 방법을 위한 양식을 문서명과 첨부파일로 나누어 정리한 표입니다.</caption>
					        <thead>
						        <tr>
                                    <th scope="col">문서명</th>
                                    <th scope="col">첨부파일</th>
						        </tr>
					        </thead>
					        <tbody>
                                <tr>
                                    <td>개인정보 열람 등 결정 이의신청서</td>
                                    <td><a aria-label="개인정보 열람 등 결정 이의신청서" class="btn" href="/open_content/main/bbs/bbsMsgFileDown.do?bcd=upfile&amp;msg_seq=87&amp;fileno=3"><span class="down">다운로드</span></a></td>
                                </tr>
					        </tbody>
				        </table>
</ul>"""
_SISUL_TABLE_IN_LI = """<ul>
<li class="ssblet">공단은 다음 개인정보파일에 대해 영향평가를 수행하였습니다.
                            <table class="data margin_t10" summary="" width="100%">
<caption>개인정보 영향평가 수행결과를 개인정보파일명, 영향평가수행연도, 이행여부로 나누어 정리한 표</caption>
<thead>
<tr>
<th scope="col">개인정보파일명</th>
<th scope="col">영향평가수행연도</th>
<th scope="col">이행여부</th>
</tr>
</thead>
<tbody>
<tr>
<td>주차료 미납과태료 관리</td>
<td>2015</td>
<td>이행</td>
</tr>
</tbody>
</table>
</li>
</ul>"""


@pytest.mark.unit
@pytest.mark.parametrize("body, cell", [
    (_SISUL_UL_DIRECT_TABLE, "개인정보 열람 등 결정 이의신청서"),
    (_SISUL_TABLE_IN_LI, "주차료 미납과태료 관리"),
], ids=["ul_direct_table", "table_in_li"])
def test_table_inside_list_is_kept_as_table(tmp_path, body, cell):
    """목록 안의 표가 표로 남고 목록 항목 텍스트에 섞이지 않는다(#567).

    <ul> 직계 <table> 은 목록 순회에서 빠져 통째로 사라졌고, <li> 안 <table> 은 셀 텍스트가
    목록 항목 한 줄로 이어 붙어 표 구조가 사라졌다.
    """
    doc = _convert(tmp_path, body)

    assert len(doc.tables) == 1
    assert cell in [c.text for c in doc.tables[0].data.table_cells]
    assert not any(cell in t.text for t in doc.texts)


# 실물 발췌: 검증 문서군 v5 web13(내부 샘플 sample_11.html, 증권사 리포트를 변환한 HTML) 첫 표.
# 자기 행 없는 <table> 이 표 하나를 감싸고 있다. 행은 앞 4행과 출처 행만 남겼다.
_REPORT_WRAPPED_TABLE = (
    "<table id = 1_5 > <table border='1'><tr><td colspan=3 rowspan=1></td></tr>"
    "<tr><td colspan=3 rowspan=1>▶ 주요 지표 일일 등락률</td></tr>"
    "<tr><td colspan=1 rowspan=1>S&P 500 6,040.53pt</td><td colspan=1 rowspan=1>-0.50%</td><td colspan=1 rowspan=1></td></tr>"
    "<tr><td colspan=1 rowspan=1>DOW 44,544.66pt</td><td colspan=1 rowspan=1>-0.75%</td><td colspan=1 rowspan=1></td></tr>"
    "<tr><td colspan=3 rowspan=1>자료: Bloomberg, 삼성증권</td></tr></table> </table>"
)


@pytest.mark.unit
def test_table_wrapping_only_a_table_keeps_inner_table(tmp_path):
    """행 없이 표 하나만 감싼 바깥 표가 빈 표로 남고 안쪽 표가 사라지던 결함(#567)."""
    doc = _convert(tmp_path, _REPORT_WRAPPED_TABLE)

    assert len(doc.tables) == 1
    assert "DOW 44,544.66pt" in [c.text for c in doc.tables[0].data.table_cells]
