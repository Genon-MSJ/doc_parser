#!/usr/bin/env python
"""루트 main.py 를 그대로 띄워 오류 응답의 사유 코드와 성격을 확인한다(#528).

모니모 실물 샘플에서 오류 입력을 만들어 /parser·/chunker 에 넣고, 응답의 `reason_code`,
`error_category`, `error_kind` 가 코드표(`manual/custom_fields_exceptions.md` 의 "사유 코드" 절)와 맞는지 단정한다.
local_main_test.py 와 같이 FastAPI TestClient 로 앱을 직접 호출하므로 네트워크가 필요 없다.

설정은 모니모 완성본의 임시 사본을 쓴다. front matter 오류는 현장 설정(on_invalid 기본
ignore)에서 나지 않으므로 사본의 product_slf 에만 `on_invalid: error` 를 켠다.

    genon/preprocessor/.venv/bin/python genon/preprocessor/tools/code_serving/error_response_check.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
MONIMO = REPO_ROOT / "genon" / "preprocessor" / "sample_files" / "monimo"
WORK = Path(tempfile.mkdtemp(prefix="error_response_check_"))


def _resource_copy() -> Path:
    """모니모 설정 사본. product_slf 의 front matter 형식 오류만 실패로 바꾼다."""
    dst = WORK / "resource"
    shutil.copytree(REPO_ROOT / "genon" / "sites" / "monimo" / "resource", dst)
    path = dst / "custom_field_product_slf.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg["source"]["pre"]["markdown"]["front_matter"]["on_invalid"] = "error"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return dst


os.environ["GENOS_RESOURCE_DIR"] = str(_resource_copy())
sys.path.insert(0, str(Path(__file__).resolve().parent))
import local_main_test  # noqa: E402,F401  더미 env 와 sys.path 설정을 재사용한다


def _derived_inputs() -> dict:
    """실물 샘플을 조금씩 손상시킨 오류 입력."""
    import openpyxl

    out: dict = {}
    out["empty"] = WORK / "empty.pdf"
    out["empty"].write_bytes(b"")

    faq = (MONIMO / "monimo_faq_json_sample.json").read_text(encoding="utf-8")
    out["broken_json"] = WORK / "monimo_faq_json_sample.json"
    out["broken_json"].write_text(faq[: len(faq) // 2], encoding="utf-8")

    hpp = json.loads((MONIMO / "monimo_product_hpp_sample.json").read_text(encoding="utf-8"))
    hpp["cardTitle"] = ""
    out["hpp_no_title"] = WORK / "monimo_product_hpp_sample.json"
    out["hpp_no_title"].write_text(json.dumps(hpp, ensure_ascii=False), encoding="utf-8")

    # 6행이 열 이름 행이다. '서비스 개요' 를 '메뉴중분류' 로 바꿔 정규화 후 중복을 만든다.
    wb = openpyxl.load_workbook(MONIMO / "monimo_menu_sample.xlsx")
    ws = wb.active
    for cell in ws[6]:
        if cell.value == "서비스 개요":
            cell.value = "메뉴중분류"
    out["menu_dup_header"] = WORK / "monimo_menu_sample.xlsx"
    wb.save(out["menu_dup_header"])

    slf = (MONIMO / "monimo_product_slf_sample.md").read_text(encoding="utf-8")
    out["slf_dup_key"] = WORK / "dup_key" / "monimo_product_slf_sample.md"
    out["slf_dup_key"].parent.mkdir()
    out["slf_dup_key"].write_text(slf.replace("---\n", "---\nauthor: \"중복\"\n", 1), encoding="utf-8")

    # `## 원문 전사` 뒤에 제외 대상 섹션만 남겨 섹션 선택 후 본문이 없게 만든다.
    sections = (MONIMO / "monimo_product_slf_sections_sample.md").read_text(encoding="utf-8")
    head = sections.split("## 원문 1쪽", 1)[0]
    out["slf_no_body"] = WORK / "no_body" / "monimo_product_slf_sections_sample.md"
    out["slf_no_body"].parent.mkdir()
    out["slf_no_body"].write_text(head, encoding="utf-8")

    out["locked_json"] = WORK / "locked.json"
    out["locked_json"].write_text("{}", encoding="utf-8")
    out["locked_json"].chmod(0)
    return out


def main() -> int:
    from fastapi.testclient import TestClient

    import main as app_main

    client = TestClient(app_main.app)
    src = _derived_inputs()

    def post(route: str, file_path, params: dict) -> dict:
        return client.post(route, json={"file_path": str(file_path), "params": params}).json()

    parsed = post("/parser", MONIMO / "monimo_product_ssf_sample.md", {"doc_type": "product_ssf"})
    assert parsed.get("code") == 0, parsed.get("errMsg")
    doc = parsed["data"]
    corrupt = {**doc, "document": {**doc["document"], "texts": "broken"}}

    # (설명, 경로, file_path, params, 기대 reason_code, 기대 error_kind)
    cases = [
        ("필수 열 부재(term 엑셀을 menu 로)", "/parser", MONIMO / "monimo_term_sample.xlsx",
         {"doc_type": "menu"}, "PARSE_D001", "permanent"),
        ("records 키 부재(cs_slf JSON 을 faq 로)", "/parser", MONIMO / "monimo_cs_slf_sample.json",
         {"doc_type": "faq"}, "PARSE_D002", "permanent"),
        ("필수 공통 필드 공백(상품명 비움)", "/parser", src["hpp_no_title"],
         {"doc_type": "product_hpp"}, "PARSE_D003", "permanent"),
        ("열 이름 중복(정규화 후)", "/parser", src["menu_dup_header"],
         {"doc_type": "menu"}, "PARSE_D004", "permanent"),
        ("front matter 중복 키", "/parser", src["slf_dup_key"],
         {"doc_type": "product_slf"}, "PARSE_D005", "permanent"),
        ("빈 파일", "/parser", src["empty"], {"doc_type": "product_ssf"}, "PARSE_D009", "permanent"),
        ("JSON 문법 손상(faq 절반)", "/parser", src["broken_json"],
         {"doc_type": "faq"}, "PARSE_D010", "permanent"),
        ("구분자 레코드 없음(md 를 cs_ssf 로)", "/parser", MONIMO / "monimo_product_ssf_sample.md",
         {"doc_type": "cs_ssf"}, "PARSE_D011", "permanent"),
        ("섹션 선택 후 본문 없음", "/parser", src["slf_no_body"],
         {"doc_type": "product_slf"}, "PARSE_D012", "permanent"),
        ("미등록 doc_type", "/parser", MONIMO / "monimo_product_ssf_sample.md",
         {"doc_type": "no_such_type"}, "PARSE_P002", "permanent"),
        ("청커 입력 누락", "/chunker", "", {}, "CHUNK_P002", "permanent"),
        ("파서 결과가 아닌 입력", "/chunker", "", {"document": {"foo": 1}}, "CHUNK_P003", "permanent"),
        ("파서 결과 손상(실물 산출 texts 손상)", "/chunker", "", {"document": corrupt},
         "CHUNK_P004", "permanent"),
        ("파서 결과 JSON 문법 손상", "/chunker", src["broken_json"], {}, "CHUNK_P004", "permanent"),
        ("입력 파일 권한 거부", "/chunker", src["locked_json"], {}, "CHUNK_S003", "permanent"),
    ]

    failures = 0
    for label, route, file_path, params, want_code, want_kind in cases:
        body = post(route, file_path, params)
        got = (body.get("reason_code"), body.get("error_kind"))
        ok = got == (want_code, want_kind) and body.get("error_category")
        failures += not ok
        print(f"[{'PASS' if ok else 'FAIL'}] {label}: reason_code={got[0]} "
              f"error_category={body.get('error_category')} error_kind={got[1]}"
              + ("" if ok else f"  기대={want_code}/{want_kind}  errMsg={str(body.get('errMsg'))[:120]}"))

    # 요청 형식 오류는 라우트 함수에 닿기 전에 FastAPI 가 거부한다.
    body = client.post("/parser", json={"params": {}}).json()
    ok = body.get("reason_code") == "COMMON_P002"
    failures += not ok
    print(f"[{'PASS' if ok else 'FAIL'}] 요청 형식 오류(file_path 누락): reason_code={body.get('reason_code')}")

    print(f"\n{len(cases) + 1 - failures}/{len(cases) + 1} 통과")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        locked = WORK / "locked.json"
        if locked.exists():
            locked.chmod(0o600)
        shutil.rmtree(WORK, ignore_errors=True)
