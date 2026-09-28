def error(sodium, message: str | BaseException, source: str = "<source>", content: str = "", position: int = 0, end: int = 1, kind: str = "syntax", frames: list[dict] | None = None) -> None:
    color, reset, bold = sodium.THEME_COLOR, sodium.Color.reset, sodium.Color.bold

    if isinstance(message, BaseException):
        exc = message
        source = getattr(exc, "filename", source)
        content = getattr(exc, "content", content)
        position = getattr(exc, "position", position)
        end = getattr(exc, "end", end)
        kind = getattr(exc, "kind", kind)
        frames = getattr(exc, "frames", frames)
        message = str(exc)

    if position < 0:
        position = 0
    if end < position:
        end = position + 1

    if frames:
        for frame in frames:
            frame_name = frame.get("name", "<anonymous>") if isinstance(frame, dict) else "<anonymous>"
            frame_source = frame.get("source", source) if isinstance(frame, dict) else source
            frame_position = frame.get("position", position) if isinstance(frame, dict) else position
            frame_end = frame.get("end", end) if isinstance(frame, dict) else end
            frame_content = content if frame is None or not isinstance(frame, dict) or not frame.get("content") else frame.get("content")
            line_number = frame_content.count("\n", 0, frame_position) + 1 if frame_content else 1
            line_start = frame_content.rfind("\n", 0, frame_position) + 1 if frame_content else 0
            line_end = frame_content.find("\n", frame_position)
            if line_end < 0:
                line_end = len(frame_content)
            line_text = frame_content[line_start:line_end] if frame_content else ""
            column = frame_position - line_start + 1
            highlight_start = max(0, frame_position - line_start)
            highlight_end = min(len(line_text), max(highlight_start + 1, frame_end - line_start))
            if (highlight_end - highlight_start) <= 1:
                arrow_len = len(line_text)
            else:
                arrow_len = max(1, highlight_end - highlight_start)
            print(f"\"{frame_source}\", line {line_number}, in {frame_name}{reset}")
            if frame_content:
                sodium.Terminal.BORDER_STYLES["custom_error"] = (
                    " ", " ", " ", " ", "~", ""
                )
                print(f"  {line_text}")
                print(f"  {' ' * highlight_start}{color}{'^' * arrow_len}{reset}")
        print(f"{color}sodium:{reset} {bold}{kind} error:{reset} {message}")
        return

    line_number = content.count("\n", 0, position) + 1
    line_start = content.rfind("\n", 0, position) + 1
    line_end = content.find("\n", position)
    if line_end < 0:
        line_end = len(content)
    line_text = content[line_start:line_end]
    column = position - line_start + 1
    highlight_start = max(0, position - line_start)
    highlight_end = min(len(line_text), max(highlight_start + 1, end - line_start))
    arrow_len = max(1, highlight_end - highlight_start)

    print(f"  {color}--> {source}:{line_number}:{column}{reset}")
    if content:
        print(f" {line_number:>3} | {line_text}")
        print(f"     | {' ' * highlight_start}{color}{'^' * arrow_len}{reset}")
