from app.core.prompts import build_rag_response_prompt, build_system_prompt


def test_system_prompt_preserves_assistant_as_subject():
    prompt = build_system_prompt("通用", "回答问题")

    assert "用户问“你”时指的是助手" in prompt
    assert "以“我/本助手”回答" in prompt
    assert "不要把用户误写成主语" in prompt


def test_system_prompt_adds_medical_emergency_guardrails():
    prompt = build_system_prompt("通用", "回答问题")

    assert "涉及医疗急救、疾病处理或用药时" in prompt
    assert "不能替代专业医护" in prompt
    assert "紧急情况应立即拨打急救电话" in prompt


def test_rag_prompt_preserves_assistant_as_subject_when_kb_hits():
    prompt = build_rag_response_prompt(kb_hit=True)

    assert "用户问“你”时指的是助手" in prompt
    assert "以“我/本助手”回答" in prompt
    assert "不要把用户误写成主语" in prompt


def test_rag_prompt_adds_medical_guardrails_when_kb_hits():
    prompt = build_rag_response_prompt(kb_hit=True)

    assert "参考资料只能作为背景信息" in prompt
    assert "不能替代专业医护" in prompt
    assert "紧急情况应立即拨打急救电话" in prompt
    assert "不要直接复述可能过时或高风险的处置建议" in prompt
    assert "不要给出确定诊断、用药顺序、注射方案或越权医疗处置" in prompt


def test_rag_hit_prompt_does_not_force_reference_prefix():
    prompt = build_rag_response_prompt(kb_hit=True)

    assert "请直接回答用户当前问题" in prompt
    assert "正文不要使用「根据参考资料」「基于知识库」这类前缀" in prompt


def test_rag_miss_prompt_does_not_force_no_knowledge_base_prefix():
    prompt = build_rag_response_prompt(kb_hit=False)

    assert "请基于通用知识自然回答当前问题" in prompt
    assert "不要使用「知识库中未找到相关内容」「以下基于通用知识回答」这类固定前缀" in prompt
    assert "开头说明：「知识库中未找到相关内容" not in prompt
