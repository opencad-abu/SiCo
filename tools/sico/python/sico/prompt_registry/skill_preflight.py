"""Single authority for SKILL preflight and eval rules."""

INSTRUCTIONS = (
    "用 check_skill 带 code 或 path 在不执行的前提下检查源码。"
    "定义函数只用 procedure(name(args) ...)；执行代码片段可用 lambda(...)、"
    "prog(...)、let(...)；不要使用 defun、nlambda 或其他函数/宏定义形式。"
    "每个 '(' 都必须以 ')' 闭合；"
    "']' 只能用于下标。注释使用 ';' 或不嵌套的 /* ... */。"
    "控制器在派发前拒绝未通过 preflight 的源码。出现 executed=false 时，"
    "修正所指出的源码并重新检查，最多两次修复尝试。通过 "
    "preflight 并不代表语法或逻辑完全正确。"
)
