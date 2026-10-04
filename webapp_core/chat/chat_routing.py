from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from webapp_core.chat import auto_runtime as auto

from webapp_core import config as cfg




class ChatRoutingMixin:
    @classmethod
    def _subject_label(cls, subject_id: str) -> str:
        return cls.SUBJECT_LABELS.get(subject_id, subject_id)

    def _build_subject_catalog(self) -> dict[str, dict[str, str]]:
        storage_root = Path(cfg.WEB_STORAGE_ROOT)
        catalog: dict[str, dict[str, str]] = {}
        for subject_id, label in self.SUBJECT_LABELS.items():
            working_dir = (storage_root / subject_id).resolve()
            catalog[subject_id] = {
                "id": subject_id,
                "label": label,
                "working_dir": str(working_dir),
            }
        return catalog



    @classmethod
    def _rank_subject_scores(
        cls, scores: dict[str, float]
    ) -> list[tuple[str, float]]:
        return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


    def _subject_route_from_scores(
        self,
        scores: dict[str, float],
        *,
        confidence: float,
        reason: str,
        requested_subjects: list[str] | None = None,
    ) -> dict[str, Any]:
        ranked = self._rank_subject_scores(scores)
        primary_subject, primary_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0
        cross_subject = primary_score >= 0.30 and second_score >= 0.30
        return {
            "scores": scores,
            "ranked": ranked,
            "primary_subject": primary_subject,
            "cross_subject": cross_subject,
            "confidence": auto._clamp_confidence(confidence),
            "reason": reason,
            "max_score": primary_score,
            "requested_subjects": list(requested_subjects or []),
        }

    def _subject_route_from_explicit_subjects(
        self, subject_ids: list[str]
    ) -> dict[str, Any]:
        scores = {subject_id: 0.0 for subject_id in self.SUBJECT_LABELS}
        base = 1.0
        for index, subject_id in enumerate(subject_ids):
            scores[subject_id] = max(0.1, base - index * 0.05)
        return self._subject_route_from_scores(
            scores,
            confidence=1.0,
            reason="用户显式指定学科",
            requested_subjects=subject_ids,
        )

    def normalize_requested_subjects(self, raw_subjects: Any) -> list[str]:
        if raw_subjects is None:
            return []
        if isinstance(raw_subjects, str):
            candidates = re.split(r"[\s,，|/]+", raw_subjects)
        elif isinstance(raw_subjects, list):
            candidates = raw_subjects
        else:
            return []

        normalized: list[str] = []
        seen: set[str] = set()
        alias_map = {
            "c": "C_program",
            "c_program": "C_program",
            "cprogram": "C_program",
            "c语言": "C_program",
            "os": "operating_systems",
            "operating_systems": "operating_systems",
            "操作系统": "operating_systems",
            "cybersec": "cybersec_lab",
            "cybersec_lab": "cybersec_lab",
            "网络安全": "cybersec_lab",
            "网络安全实验": "cybersec_lab",
        }
        for item in candidates:
            value = str(item or "").strip()
            if not value:
                continue
            subject_id = alias_map.get(value.lower(), value)
            if subject_id in self.subject_catalog and subject_id not in seen:
                seen.add(subject_id)
                normalized.append(subject_id)
        return normalized


    @staticmethod
    def _normalize_for_exact_match(text: str) -> str:
        value = str(text or "").strip()
        if not value:
            return ""
        value = re.sub(r"\s+", "", value)
        value = value.strip("`*_~\"'“”‘’[](){}<>")
        value = re.sub(r"[。！？!?，,、；;：:\.\-—\s]+$", "", value)
        return value.lower()

    @classmethod
    def _strip_leading_question_echo(cls, answer: str, question: str) -> str:
        raw_answer = str(answer or "")
        raw_question = str(question or "")
        if not raw_answer.strip() or not raw_question.strip():
            return raw_answer

        question_norm = cls._normalize_for_exact_match(raw_question)
        if not question_norm:
            return raw_answer

        lines = raw_answer.splitlines()
        scan_limit = min(len(lines), 4)
        for idx in range(scan_limit):
            line = lines[idx].strip()
            if not line:
                continue
            candidate = line
            candidate = re.sub(r"^#{1,6}\s*", "", candidate)
            candidate = re.sub(
                r"^(问题|用户问题|提问|question|q)\s*[:：]\s*",
                "",
                candidate,
                flags=re.IGNORECASE,
            )
            candidate = candidate.strip("`*_~\"'“”‘’[](){}<>")
            candidate_norm = cls._normalize_for_exact_match(candidate)
            if candidate_norm and candidate_norm == question_norm:
                next_idx = idx + 1
                while next_idx < len(lines) and not lines[next_idx].strip():
                    next_idx += 1
                return "\n".join(lines[next_idx:]).lstrip()
            break
        return raw_answer

    def _build_direct_answer_prompt(
        self,
        *,
        user_question: str,
        augmented_question: str,
        mode: str,
        thread_id: str,
        response_language: str = "zh",
        requested_subjects: list[str] | None = None,
    ) -> str:
        scope = "、".join(self._subject_label(s) for s in (requested_subjects or []))
        return (
            "你是问答助手。当前处于免检索直答阶段。\n"
            "请仅基于用户问题、当前会话上下文和你已有通用知识回答，不调用外部检索。\n"
            "要求：\n"
            "1) 使用 Markdown 输出，默认聚焦问题简洁作答，不主动扩展为完整教程；用户要求详细分析或逐步推导时完整展开；\n"
            "2) 如无法确定事实或涉及时效/来源，必须明确说明不确定并建议改走检索；\n"
            "3) 不要编造来源；课程特定事实缺少依据时说明未知或追问，不得用通用知识冒充课程规定；\n"
            "4) 不要复述用户原问题，不要把原问题当标题。\n"
            "5) 代码分析和解题辅导直接根据已提供内容展开；缺少关键代码、题干或参数时先询问，不要编造输入。\n"
            "6) 没有实际执行代码时，不得声称已编译、运行或通过测试；区分推断结果和实测结果。\n\n"
            f"回答语言要求：{self._response_language_instruction(response_language)}\n\n"
            f"当前模式：{mode}\n"
            f"课程范围：{scope or '未指定'}（仅作为理解问题的上下文，不代表已查询课程资料）\n"
            f"thread_id：{thread_id}\n"
            f"用户问题：{user_question}\n"
            f"上下文增强问题：{augmented_question}"
        )
