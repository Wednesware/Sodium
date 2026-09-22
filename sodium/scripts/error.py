def error(sodium, message: str, source: str = "<source>", content: str = "", position: int = 0, end: int = 1, kind: str = "syntax") -> None:
    color, reset, bold = sodium.THEME_COLOR, sodium.Color.reset, sodium.Color.bold
    line = content.count("\n", 0, position) + 1
    start = content.rfind("\n", 0, position) + 1
    line_end = content.find("\n", position)
    column = position - start + 1
    print(f"{color}sodium:{reset} {bold}{kind} error:{reset} {message}")
    print(f"  {color}--> {source}:{line}:{column}{reset}")
    if content:
        print(f" {line:>3} | {content[start:line_end if line_end >= 0 else len(content)]}")
        print(f"     | {' ' * (column - 1)}{color}{'^' * max(1, min(end, line_end if line_end >= 0 else len(content)) - position)}{reset}")