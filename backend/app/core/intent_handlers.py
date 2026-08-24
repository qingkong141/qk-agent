from app.core.input_intent import InputIntent


INTENT_RESPONSES = {
    InputIntent.DOCTOR_CAPABILITY: (
        "如果你想了解我在医疗方向能做什么，我可以提供基础健康知识解释、"
        "常见急救原则说明、就医沟通建议、医学资料摘要和风险提示。"
        "我不能替代医生诊断、开药或制定治疗方案；遇到急症应立即联系急救或专业医护。"
    ),
    InputIntent.NURSE_CAPABILITY: (
        "如果你想了解护士或护理方向，我可以介绍护理岗位常见职责、"
        "患者沟通要点、基础护理常识、护理记录或宣教材料的整理思路。"
        "我不能替代护士或医生进行现场评估、执行医嘱或处理紧急病情；"
        "涉及具体患者照护时应以专业医护人员判断为准。"
    ),
    InputIntent.MEDICAL_STAFF_CAPABILITY: (
        "如果你想了解医生和护士相关内容，我可以分别介绍医生的诊疗职责、"
        "护士的护理职责、医护协作流程、患者沟通要点，以及两类岗位的区别。"
        "我不能替代专业医护做诊断、开药、执行医嘱或现场评估；"
        "涉及具体病情时应咨询医生或护士。"
    ),
    InputIntent.UNDERSPECIFIED_MEDICAL: (
        "我可以介绍基础医疗急救知识。你想了解哪一类场景？"
        "例如窒息、严重出血、中暑、一氧化碳中毒、触电，或疑似中风。"
        "如果是正在发生的紧急情况，请立即拨打急救电话。"
    ),
}


def resolve_intent_response(intent: InputIntent, message: str = "") -> str | None:
    if intent == InputIntent.NO_OP:
        return "好的，有需要随时告诉我。"
    if intent == InputIntent.HIGH_RISK_CLINICAL_ACTION:
        return (
            "调整输液滴速属于高风险医疗/护理操作，不能根据一句话直接给出操作步骤或具体数值。"
            "如果是在真实患者现场，请立即联系护士或医生，按医嘱和所在机构流程处理；不要自行调节输液泵或输液夹。"
            "如果你只是想学习相关知识，可以补充场景，例如：成人/儿童、药物类型、是否有医嘱、当前和目标滴速，我可以说明安全注意事项和需要确认的信息。"
        )
    if intent == InputIntent.UNCLEAR_INPUT:
        text = message.strip()
        if text:
            return f"我不太确定你想问的是“{text}”哪方面。可以换个说法，或补充你想了解的对象/问题吗？"
        return "我不太确定你想问哪方面。可以换个说法，或补充你想了解的对象/问题吗？"
    if intent == InputIntent.SHORT_CLARIFICATION:
        topic = message.strip()
        if topic:
            return f"你想了解“{topic}”的哪方面？可以补充一下具体问题。"
        return "你想了解哪方面？可以补充一下具体问题。"
    if intent == InputIntent.RESUME_INTERRUPTED:
        return "没有可继续的上一段回答。你可以直接提出新的问题。"
    if intent == InputIntent.INTERRUPTED_FOLLOWUP:
        return "我没有找到可衔接的上一段回答。请直接说明你想调整或了解的内容。"
    return INTENT_RESPONSES.get(intent)
