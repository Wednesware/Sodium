def error(sodium, message: str | BaseException, source: str = "<source>", content: str = "", position: int = 0, end: int = 1, kind: str = "syntax", frames: list[dict] | None = None) -> None:
    color, reset, bold = sodium.THEME_COLOR, sodium.Color.reset, sodium.Color.bold

    def _source_name(value):
        return str(value).strip() if value not in (None, "") else "<source>"

    def _line_bounds(raw_position: int, raw_end: int, line_text: str) -> tuple[int, int]:
        line_length = len(line_text)
        if line_length == 0:
            return 0, 0
        start = max(0, min(raw_position, line_length - 1))
        end = max(start + 1, min(raw_end, line_length))
        if end <= start:
            end = min(line_length, start + 1)
        return start, end

    def _print_location(source_name: str, line_number: int, column: int, line_text: str, highlight_start: int = 0, highlight_end: int = 1, show_highlight: bool = True) -> None:
        print(f"  {color}--> {source_name}:{line_number}:{column}{reset}")
        if line_text:
            print(f" {line_number:>3} | {line_text}")
            if show_highlight:
                print(f"     | {' ' * highlight_start}{color}{'^' * max(1, highlight_end - highlight_start)}{reset}")
        else:
            print(f" {line_number:>3} | <source not available>")
            if show_highlight:
                print(f"     | {color}^{reset}")

    if isinstance(message, BaseException):
        exc = message
        source = getattr(exc, "filename", None) or source
        content = getattr(exc, "content", None) or content
        position = getattr(exc, "position", position)
        end = getattr(exc, "end", end)
        kind = getattr(exc, "kind", kind)
        frames = getattr(exc, "frames", frames)
        message = str(exc)

    source = _source_name(source)

    if position < 0:
        position = 0
    if end < position:
        end = position + 1

    if frames:
        for frame in frames:
            frame_name = frame.get("name", "<anonymous>") if isinstance(frame, dict) else "<anonymous>"
            frame_source = _source_name(frame.get("source", source) if isinstance(frame, dict) else source)
            frame_position = frame.get("position", position) if isinstance(frame, dict) else position
            frame_end = frame.get("end", end) if isinstance(frame, dict) else end
            frame_content = content if frame is None or not isinstance(frame, dict) or not frame.get("content") else frame.get("content")
            frame_trigger_name = frame.get("trigger_name") if isinstance(frame, dict) else None
            show_highlight = not bool(frame_trigger_name)
            if not frame_content:
                frame_line_number = 1
                frame_column = max(1, int(frame_position) + 1)
                print(f'{color}{frame_source}:{frame_line_number} in {frame_name}{reset}')
                _print_location(frame_source, frame_line_number, frame_column, "", 0, 1, show_highlight)
                continue
            lines = frame_content.splitlines()
            line_number = frame_content.count("\n", 0, frame_position) + 1
            line_start = frame_content.rfind("\n", 0, frame_position) + 1
            line_end = frame_content.find("\n", frame_position)
            if line_end < 0:
                line_end = len(frame_content)
            start_index = max(0, line_number - 2)
            end_index = min(len(lines), line_number + 1)
            print(f'{color}{frame_source}:{line_number} in {frame_name}{reset}')
            for idx in range(start_index, end_index):
                snippet_line = lines[idx]
                if idx + 1 == line_number:
                    local_start, local_end = _line_bounds(frame_position - line_start, frame_end - line_start, snippet_line)
                    print(f'{idx + 1:>4} | {snippet_line}')
                    if show_highlight:
                        print(f"     | {' ' * local_start}{color}{'^' * max(1, local_end - local_start)}{reset}")
                else:
                    print(f'{idx + 1:>4} | {snippet_line}')
        print(f"{color}sodium:{reset} {bold}{kind} error:{reset} {message}")
        return

    if not content:
        line_number = 1
        column = max(1, int(position) + 1)
        _print_location(source, line_number, column, "", 0, 1)
        print(f"{color}sodium:{reset} {bold}{kind} error:{reset} {message}")
        return

    line_number = content.count("\n", 0, position) + 1
    line_start = content.rfind("\n", 0, position) + 1
    line_end = content.find("\n", position)
    if line_end < 0:
        line_end = len(content)
    line_text = content[line_start:line_end]
    highlight_start, highlight_end = _line_bounds(position - line_start, end - line_start, line_text)
    column = highlight_start + 1

    _print_location(source, line_number, column, line_text, highlight_start, highlight_end)
    print(f"{color}sodium:{reset} {bold}{kind} error:{reset} {message}")
