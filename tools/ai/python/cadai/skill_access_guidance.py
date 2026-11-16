"""Single authority for Cadence object property-access guidance.

Grounded in:
skdfref/topic/Retrieving_the_List_of_Attribute_Names__Attributes_or_Values_for_a_dbObject
sklanguser/topic/Access_Operators_in_SKILL
"""

INSTRUCTIONS = (
    "探索或读写 Cadence 对象属性时优先使用访问运算符，不要习惯性改用 "
    "dbGet/get 或硬编码字段列表：obj~>prop 读取属性值，obj~>prop=value 写入。"
    "未知 dbObject 先用 obj~>? 列出可用属性名，再用 obj~>?? 查看属性名-值 "
    "DPL（disembodied property list）；"
    "-> 用于 DPL、defstruct、关联表和用户类型，也可用 obj->? / obj->?? 查看槽位或键；"
    "~> 还能遍历对象列表并逐项访问，例如 cv~>instances~>name。"
    "先用参考或小 probe 确认对象类型、返回结构和可写性；"
    "?/?? 可能产生较大的 DPL，应按需筛选，不要无界打印。"
)
