"""#481 — 프로세서 인스턴스에 요청 상태를 쓰던 공유 상태 제거.

결함: 같은 인스턴스로 문서 둘을 동시에 처리하면(HTTP 의 await 지점, 스레드) 나중 요청이 쓴 값을
먼저 요청이 읽었다. 재현 R1~R4 가 각각 한 자리다.
  R1 파싱 토글(enricher 3종)   R2 청킹 결과(쪽별 청크 수·표 조각)   R3 루트 로거   R4 docling 전역 page_batch_size
외부 호출 없음. DocumentProcessor 는 기동하지 않는다(R2 만 예외 — 아래 docstring).
"""
import logging
import random
import sys
from collections import defaultdict
from pathlib import Path

import pytest
from docling_core.types.doc import BoundingBox, DocItemLabel, DoclingDocument, ProvenanceItem, Size

from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.datamodel.settings import settings as docling_settings
from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline
from facade import chunking_processor as chunker_facade
from facade import parser_processor as parser_facade
from genon.preprocessor.processing.common import runtime as rt
from genon.preprocessor.processing.common import runtime_kwargs as rk
from genon.preprocessor.processing.common import job as jb
from genon.preprocessor.processing.enrichment.doc_summary import DocSummaryOptions
from genon.preprocessor.processing.enrichment.image_description import ImageDescriptionOptions
from genon.preprocessor.processing.enrichment.table_description import TableDescriptionOptions


# ─── R1 파싱: 요청 토글은 job 에 산다 ───────────────────────────────────────

class _Intel:
    """기동을 우회한 _intel — 기준 옵션 셋은 켜져 있고(요청이 끌 수 있게), 나머지는 읽기 전용 값."""

    def __init__(self):
        url = "http://unused.invalid/v1"
        self._runtime_cfg = {}
        self._base_image_description_options = ImageDescriptionOptions(enabled=True, api_url=url)
        self._base_table_description_options = TableDescriptionOptions(enabled=True, api_url=url)
        self._base_doc_summary_options = DocSummaryOptions(enabled=True, api_url=url)
        self.pipe_line_options = type("P", (), {"do_picture_classification": False})()
        self.enrichment_options = type("E", (), {"do_toc_enrichment": False})()
        self.custom_fields_cfgs = ()

    def _normalize_runtime_kwargs(self, kwargs):
        return rk.normalize_runtime_kwargs(self, kwargs)

    def _request_enrichers(self, kwargs):
        return rk.build_request_enrichers(self, kwargs)


def _parser_with_overlay(overlay: dict):
    cls = type("_P", (parser_facade.DocumentProcessor,), {"CONFIG_BY_DOC_TYPE": overlay})
    proc = object.__new__(cls)
    proc._ext_aliases = {}
    proc._intel = _Intel()
    return proc


@pytest.mark.unit
def test_parse_jobs_keep_their_own_enrichers(tmp_path: Path):
    """재현 R1: doc_type 오버레이가 반대인 요청 둘을 같은 인스턴스로 시작해도 각자의 토글을 본다.
    인스턴스(_intel)에는 enricher 가 생기지 않는다 — 요청 묶음은 job.params["_enrichers"] 에만."""
    proc = _parser_with_overlay({
        "on": {"table_desc": 1, "img_desc": 1, "doc_summary": 1},
        "off": {"table_desc": 0, "img_desc": 0, "doc_summary": 0},
    })
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("본문\n", encoding="utf-8"); b.write_text("본문\n", encoding="utf-8")

    job_a = proc._start_job(None, str(a), doc_type="on")     # A 가 await 하는 사이 …
    job_b = proc._start_job(None, str(b), doc_type="off")    # … B 가 시작한다 (예전엔 여기서 A 의 enricher 가 덮였다)
    try:
        ea, eb = job_a.params["_enrichers"], job_b.params["_enrichers"]
        assert ([ea.table.options.enabled, ea.image.options.enabled, ea.doc_summary.options.enabled],
                [eb.table.options.enabled, eb.image.options.enabled, eb.doc_summary.options.enabled]) == \
               ([True, True, True], [False, False, False])
        assert not any(hasattr(proc._intel, n) for n in
                       ("image_description_enricher", "table_description_enricher", "doc_summary_enricher"))
    finally:
        proc._finish_job(job_a); proc._finish_job(job_b)


# ─── R2 청킹: 분할 결과는 job.notes["split"] 에 산다 ─────────────────────────

def _doc(name: str, pages: int, per_page: int) -> DoclingDocument:
    d = DoclingDocument(name=name)
    words = ["계약", "보험", "약관", "지급", "청구", "기간", "해지", "환급", "보장", "특약", "고객", "상품"]
    for pg in range(1, pages + 1):
        d.add_page(page_no=pg, size=Size(width=600, height=800))
        for k in range(per_page):
            rnd = random.Random(f"{name}{pg}{k}")
            txt = f"{name} {pg}쪽 {k}번 문단. " + " ".join(rnd.choice(words) + str(rnd.randint(1, 999)) for _ in range(60)) + "."
            d.add_text(label=DocItemLabel.TEXT, text=txt,
                       prov=ProvenanceItem(page_no=pg, bbox=BoundingBox(l=0, t=k * 10, r=500, b=k * 10 + 9),
                                           charspan=(0, len(txt))))
    return d


@pytest.mark.unit
def test_chunk_jobs_keep_their_own_split_info():
    """재현 R2: 쪽별 청크 수가 다른 문서 둘을 같은 인스턴스로 번갈아 분할해도 각 job 이 자기 값을 본다.

    청킹 본체(토크나이저·분할기)가 실제로 돌아야 하는 재현이라 기본 설정으로 청커를 기동한다
    (char 토크나이저, 외부 호출 없음, 생성 ~15ms). 설정 배선을 보는 테스트가 아니다.
    """
    proc = chunker_facade.DocumentProcessor()
    x, y = _doc("X", pages=1, per_page=12).export_to_dict(), _doc("Y", pages=6, per_page=1).export_to_dict()

    def job(data):
        return jb.ChunkJob(request=None, file_path="", kind="docling", data=data, doc_type=None, params={"chunk_size": 300})

    def counts(j):
        return dict(j.notes["split"].page_chunk_counts)

    solo_x, solo_y = job(x), job(y)                   # 기준: 각자 혼자 분할했을 때의 쪽별 청크 수
    proc.split_document(solo_x); proc.split_document(solo_y)

    job_x, job_y = job(x), job(y)
    proc.split_document(job_x)
    proc.split_document(job_y)                        # 예전엔 여기서 X 의 page_chunk_counts 가 Y 값으로 덮였다
    assert (counts(job_x), counts(job_y)) == (counts(solo_x), counts(solo_y))
    assert counts(solo_x)[1] > 1 and len(counts(solo_y)) > 1          # 두 문서의 값이 실제로 다르다
    assert dict(getattr(proc, "page_chunk_counts", {})) == {}         # 인스턴스에는 쓰지 않는다


# ─── R3 로거: 요청이 프로세스 로깅을 끄지 않는다 ─────────────────────────────

@pytest.mark.unit
def test_setup_logging_restores_disable():
    """재현 R3: 0(NOLOG) 뒤에 양수 레벨을 주면 logging.disable 이 풀린다(예전엔 영구히 꺼졌다)."""
    saved = (logging.root.level, logging.root.manager.disable, list(logging.root.handlers))
    try:
        rt.setup_logging(0, announce=lambda *_: None)
        rt.setup_logging(4, announce=lambda *_: None)
        assert logging.root.manager.disable == logging.NOTSET
    finally:
        logging.disable(saved[1]); logging.root.setLevel(saved[0]); logging.root.handlers[:] = saved[2]


@pytest.mark.unit
def test_cli_log_level_applies_at_process_startup(tmp_path: Path):
    """요청 log_level 을 core 가 무시하게 되면서 CLI 의 --log-level 이 죽었던 것(리뷰 지적) — CLI 는
    프로세스에 직접 적용한다. 프로세서는 가짜(기동·처리 없음)."""
    from processing.core import cli as core_cli

    seen = {}

    class _Fake:
        def __init__(self, config_path=None):
            pass

        def setup_logging(self, level_num):
            seen["level"] = level_num

        async def __call__(self, request, file_path, **kwargs):
            seen["kwargs"] = kwargs
            return {"elements": []}

    out = tmp_path / "o.json"
    assert core_cli.run_cli(_Fake, [str(tmp_path / "a.txt"), "--log-level", "0", "-o", str(out)]) == 0
    assert (seen["level"], seen["kwargs"].get("log_level")) == (0, 0)


# ─── R4 page_batch_size: 전역이 아니라 파이프라인 옵션 ─────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("option,expected", [
    (128, 128),
    (None, docling_settings.perf.page_batch_size),   # 옵션이 없으면 전역 기본값
])
def test_page_batch_size_comes_from_pipeline_options(option, expected):
    """재현 R4: 파서 128·청커 32 가 전역 하나를 두고 덮어쓰던 것을 파이프라인 옵션으로 가른다."""
    pipeline = object.__new__(StandardPdfPipeline)
    pipeline.pipeline_options = PdfPipelineOptions(page_batch_size=option)
    assert pipeline._page_batch_size() == expected
