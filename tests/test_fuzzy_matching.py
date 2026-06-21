"""模糊匹配工具函数测试."""

from __future__ import annotations

import pytest

from umu_sdk.adapters.mcp.utils import (
    compute_similarity,
    fuzzy_filter_items,
    fuzzy_filter_items_multi_key,
)


class TestComputeSimilarity:
    """compute_similarity 测试."""

    def test_exact_match(self) -> None:
        assert compute_similarity("B-A-1", "B-A-1") == 1.0

    def test_case_insensitive(self) -> None:
        assert compute_similarity("b-a-1", "B-A-1") == 1.0

    def test_whitespace_trimmed(self) -> None:
        assert compute_similarity("  B-A-1  ", "B-A-1") == 1.0

    def test_partial_match(self) -> None:
        score = compute_similarity("B-A-1", "B_A_1")
        assert 0.0 < score < 1.0

    def test_chinese_match(self) -> None:
        score = compute_similarity("销售部", "销售一部")
        assert 0.0 < score < 1.0

    def test_no_match(self) -> None:
        score = compute_similarity("B-A-1", "财务部")
        assert 0.0 <= score < 0.3

    def test_empty_query(self) -> None:
        assert compute_similarity("", "B-A-1") == 0.0

    def test_empty_target(self) -> None:
        assert compute_similarity("B-A-1", "") == 0.0


class TestFuzzyFilterItems:
    """fuzzy_filter_items 测试."""

    @pytest.fixture
    def departments(self) -> list[dict]:
        return [
            {"department_id": "1", "department_name": "销售一部"},
            {"department_id": "2", "department_name": "销售二部"},
            {"department_id": "3", "department_name": "财务部"},
            {"department_id": "4", "department_name": "研发部"},
        ]

    def test_exact_match(self, departments: list[dict]) -> None:
        result = fuzzy_filter_items(
            departments, "销售一部", key="department_name", top_k=10
        )
        assert len(result) >= 1
        assert result[0]["department_id"] == "1"
        assert result[0]["_similarity_score"] == 1.0

    def test_fuzzy_match(self, departments: list[dict]) -> None:
        result = fuzzy_filter_items(
            departments, "销售一部", key="department_name", top_k=10
        )
        assert len(result) == 2
        assert result[0]["department_name"] == "销售一部"
        assert result[1]["department_name"] == "销售二部"
        assert result[0]["_similarity_score"] >= result[1]["_similarity_score"]

    def test_top_k(self, departments: list[dict]) -> None:
        result = fuzzy_filter_items(
            departments, "销售", key="department_name", top_k=2
        )
        assert len(result) == 2

    def test_threshold(self, departments: list[dict]) -> None:
        result = fuzzy_filter_items(
            departments,
            "销售一部",
            key="department_name",
            top_k=10,
            similarity_threshold=0.9,
        )
        assert len(result) == 1
        assert result[0]["department_name"] == "销售一部"

    def test_no_match(self, departments: list[dict]) -> None:
        result = fuzzy_filter_items(
            departments, "不存在的部门", key="department_name", top_k=10
        )
        assert len(result) == 0

    def test_empty_query(self, departments: list[dict]) -> None:
        result = fuzzy_filter_items(
            departments, "", key="department_name", top_k=10
        )
        assert len(result) == 4

    def test_callable_key(self, departments: list[dict]) -> None:
        result = fuzzy_filter_items(
            departments,
            "1",
            key=lambda item: item["department_id"],
            top_k=10,
        )
        assert len(result) == 1
        assert result[0]["department_id"] == "1"

    def test_input_not_mutated(self, departments: list[dict]) -> None:
        original_ids = [d["department_id"] for d in departments]
        fuzzy_filter_items(
            departments, "销售", key="department_name", top_k=10
        )
        assert [d["department_id"] for d in departments] == original_ids
        assert "_similarity_score" not in departments[0]


class TestFuzzyFilterItemsMultiKey:
    """fuzzy_filter_items_multi_key 测试."""

    @pytest.fixture
    def categories(self) -> list[dict]:
        return [
            {"id": "1", "name": "安全培训", "path": "培训/安全"},
            {"id": "2", "name": "管理培训", "path": "培训/管理"},
            {"id": "3", "name": "财务制度", "path": "制度/财务"},
        ]

    def test_match_name(self, categories: list[dict]) -> None:
        result = fuzzy_filter_items_multi_key(
            categories, "安全", keys=["name", "path"], top_k=10
        )
        assert len(result) >= 1
        assert result[0]["id"] == "1"

    def test_match_path(self, categories: list[dict]) -> None:
        result = fuzzy_filter_items_multi_key(
            categories, "财务", keys=["name", "path"], top_k=10
        )
        assert len(result) >= 1
        ids = {item["id"] for item in result}
        assert "3" in ids

    def test_take_best_score(self, categories: list[dict]) -> None:
        result = fuzzy_filter_items_multi_key(
            categories, "培训", keys=["name", "path"], top_k=10
        )
        assert len(result) == 2
        for item in result:
            assert item["_similarity_score"] > 0

    def test_callable_keys(self, categories: list[dict]) -> None:
        result = fuzzy_filter_items_multi_key(
            categories,
            "安全",
            keys=[lambda item: item["name"], lambda item: item["path"]],
            top_k=10,
        )
        assert len(result) >= 1
