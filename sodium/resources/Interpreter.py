import importlib
import os
import re
import tempfile
import urllib.request
from pathlib import Path
from urllib.parse import urlparse


class SodiumError(RuntimeError):
    def __init__(self, message: str, *, kind: str = "runtime", filename: str = "<source>", position: int = 0, end: int | None = None, frames: list[dict] | None = None, content: str = "") -> None:
        super().__init__(message)
        self.kind = kind
        self.message = str(message)
        self.filename = filename
        self.position = position
        self.end = position + 1 if end is None else end
        self.frames = list(frames or [])
        self.content = content

    def __str__(self) -> str:
        return self.message


class Interpreter:
    _trigger_registry: dict[str, list[dict]] = {}
    _trigger_scan_roots_done: set[str] = set()
    _module_cache: dict[str, dict] = {}

    def __init__(self, sodium, options: list[str]) -> None:
        self.sodium = sodium
        self.options = options
        self.scopes: list[dict] = []
        self.content = ""
        self.filename = "<source>"
        self.path = ""
        self.error_position = 0
        self.error_end = 1
        self.call_stack: list[dict] = []
        self._trigger_dispatching: set[str] = set()
        self._end_trigger_fired = False

    def run(self, tokens: list[list[dict]], content: str = "", filename: str = "<source>", path: str = "", argv: list[str] | None = None):
        self.__class__._trigger_registry.clear()
        self.__class__._trigger_scan_roots_done.clear()
        self.__class__._module_cache.clear()
        self.content = content
        self.filename = filename
        self.path = path or filename if filename not in {"<source>", ""} else ""
        self.scopes = [self.builtins(), {}]
        self._end_trigger_fired = False
        if argv is not None:
            self.scopes[-1]["argv"] = list(argv)
        self.call_stack = [{"name": "main", "source": self.filename or "<source>", "position": 0, "end": 1, "content": self.content}]
        try:
            value = self.lines(tokens)
            if isinstance(value, tuple) and value[0] == "return":
                return value[1]
            if not self._end_trigger_fired:
                self._builtin_trigger("end")
                self._end_trigger_fired = True
            return 0
        except Exception:
            if not self._end_trigger_fired:
                self._end_trigger_fired = True
                try:
                    self._builtin_trigger("end")
                except Exception:
                    pass
            raise
        finally:
            if self.call_stack:
                self.call_stack.pop()

    def _sodium_type_name(self, python_name: str) -> str:
        return {
            "str": "string",
            "int": "number",
            "float": "number",
            "bool": "boolean",
            "list": "array",
            "tuple": "array",
            "dict": "map",
            "set": "set",
            "bytes": "bytes",
        }.get(python_name, python_name)

    def _translate_type_names(self, message: str) -> str:
        if not isinstance(message, str):
            return str(message)

        quoted = re.compile(r"(['\"])(int|str|float|bool|list|tuple|dict|set|bytes)(\1)")
        message = quoted.sub(lambda match: f"{match.group(1)}{self._sodium_type_name(match.group(2))}{match.group(3)}", message)

        bare = re.compile(r"(?<![A-Za-z_])(int|str|float|bool|list|tuple|dict|set|bytes)(?![A-Za-z_])")
        return bare.sub(lambda match: self._sodium_type_name(match.group(1)), message)

    def fail(self, token: dict | None, message: str, kind=RuntimeError):
        token = token or {}
        self.mark(token)
        if isinstance(kind, type):
            kind_name = kind.__name__
        else:
            kind_name = str(kind)
        if kind_name == "SyntaxError":
            error_kind = "syntax"
        elif kind_name == "NameError":
            error_kind = "name"
        else:
            error_kind = "runtime"
        frames = [dict(frame) for frame in self.call_stack]
        if frames:
            frames[-1]["position"] = self.error_position
            frames[-1]["end"] = self.error_end
            frames[-1]["source"] = self.filename or frames[-1].get("source", "<source>")
            frames[-1].setdefault("content", self.content)
        else:
            frames = [{"name": "main", "source": self.filename or "<source>", "position": self.error_position, "end": self.error_end, "content": self.content}]
        raise SodiumError(
            self._translate_type_names(message),
            kind=error_kind,
            filename=self.filename or "<source>",
            position=self.error_position,
            end=self.error_end,
            frames=frames,
            content=self.content,
        )

    def mark(self, token: dict):
        if not token:
            return
        if "position" in token:
            self.error_position = token["position"]
            self.error_end = token.get("end", token["position"] + 1)
            return
        children = [child for child in token.get("children", []) if isinstance(child, dict)]
        if children:
            self.error_position = children[0].get("position", self.error_position)
            self.error_end = children[-1].get("end", self.error_position + 1)

    def _resolve_file_context(self, filename: str | None = None, path: str | None = None):
        file_path = ""
        candidate = filename or path or self.filename or self.path or ""
        if candidate not in {"<source>", ""}:
            file_path = str(Path(candidate).resolve())
        dir_path = str(Path(file_path).parent) if file_path else ""
        return file_path, dir_path

    def builtins(self) -> dict:
        file_path, dir_path = self._resolve_file_context(self.filename, self.path)

        builtins = {
            "print": self._builtin_print,
            "input": self._builtin_input,
            "len": self._builtin_len,
            "range": self._builtin_range,
            "string": self._builtin_string,
            "number": self._builtin_number,
            "boolean": self._builtin_boolean,
            "array": self._builtin_array,
            "map": self._builtin_map,
            "set": self._builtin_set,
            "bytes": self._builtin_bytes,
            "open": self._builtin_open,
            "read_file": self._builtin_read_file,
            "write_file": self._builtin_write_file,
            "append_file": self._builtin_append_file,
            "fetch": self._builtin_fetch,
            "import": self._builtin_import,
            "import_python": self._builtin_import_python,
            "on": self._builtin_on,
            "trigger": self._builtin_trigger,
            "await": self._builtin_await,
            "null": None,
            "function": {"kind": "function", "name": "function", "params": [], "body": [], "scope": []},
            "class": {"kind": "class", "name": "class", "methods": {}},
            "super": {"kind": "super_factory", "name": "super"},
            "if": {"kind": "condition", "name": "if", "value": False},
            "unless": {"kind": "condition", "name": "unless", "value": False},
            "elseif": {"kind": "condition", "name": "elseif", "value": False},
            "else": {"kind": "condition", "name": "else", "value": False},
            "while": {"kind": "loop", "name": "while", "value": False},
            "for": {"kind": "for_loop", "name": "for", "value": False},
            "match": {"kind": "match", "name": "match", "value": False},
            "case": {"kind": "case", "name": "case", "value": False},
            "return": {"kind": "return", "name": "return", "value": False},
            "break": {"kind": "break", "name": "break", "value": False},
            "continue": {"kind": "continue", "name": "continue", "value": False},
            "true": True,
            "false": False,
            "_this": file_path,
            "_here": dir_path
        }
        return builtins

    def builtins_for_file(self, filename: str | None = None, path: str | None = None):
        file_path, dir_path = self._resolve_file_context(filename, path)
        builtins = self.builtins()
        builtins["_this"] = file_path
        builtins["_here"] = dir_path
        return builtins

    def _fmt_value(self, value, depth=0):
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, dict):
            if "__class__" in value and "kind" not in value:
                class_def = value["__class__"]
                name = class_def.get("name") if isinstance(class_def, dict) else "instance"
                return f"<instance {name}>"
            kind = value.get("kind")
            if kind == "function":
                name = value.get("name") or value.get("__name__") or "anonymous"
                return f"<function {name}>"
            if kind == "class":
                name = value.get("name") or value.get("__name__") or "anonymous"
                return f"<class {name}>"
            if kind == "condition":
                name = value.get("name") or value.get("__name__") or "anonymous"
                return f"<condition {name}>"
            if not value:
                return "{}"
            pieces = []
            for key, item in value.items():
                pieces.append(f"{self._fmt_value(key, depth + 1)}: {self._fmt_value(item, depth + 1)}")
            return "{" + ", ".join(pieces) + "}"
        if isinstance(value, list):
            if not value:
                return "[]"
            return "[" + ", ".join(self._fmt_value(item, depth + 1) for item in value) + "]"
        if isinstance(value, tuple):
            if not value:
                return "()"
            return "(" + ", ".join(self._fmt_value(item, depth + 1) for item in value) + ")"
        if isinstance(value, set):
            if not value:
                return "{}"
            return "{" + ", ".join(self._fmt_value(item, depth + 1) for item in sorted(value, key=lambda item: str(item))) + "}"
        return str(value)

    def _builtin_print(self, *values):
        print(*[self._fmt_value(value) for value in values])
        return None

    def _builtin_input(self, *values):
        prompt = "".join(str(value) for value in values)
        try:
            return input(prompt)
        except EOFError as exc:
            raise RuntimeError("unexpected end of input") from exc
        except KeyboardInterrupt as exc:
            raise RuntimeError("interrupted by user") from exc

    def _builtin_len(self, value):
        return len(value)

    def _builtin_range(self, *values):
        return range(*values)

    def _builtin_string(self, value):
        return self._fmt_value(value)

    def _builtin_number(self, value):
        return float(value)

    def _builtin_boolean(self, value):
        return bool(value)

    def _builtin_array(self, *values):
        return list(values)

    def _builtin_map(self, *values):
        if not values:
            return {}
        if len(values) == 1 and isinstance(values[0], dict):
            return dict(values[0])
        return dict(values)

    def _builtin_set(self, *values):
        if not values:
            return set()
        if len(values) == 1 and isinstance(values[0], (list, tuple, set)):
            return set(values[0])
        return set(values)

    def _builtin_bytes(self, value):
        return bytes(value)

    def _builtin_open(self, path, mode: str | None = None, encoding: str = "utf-8"):
        file_path = str(path)
        if mode is None:
            mode = "r+" if Path(file_path).exists() else "a+"
        mode = str(mode)
        handle = open(file_path, mode, encoding=None if "b" in mode else encoding)
        methods = {
            "kind": "file",
            "path": file_path,
            "mode": mode,
            "encoding": encoding,
            "handle": handle,
            "name": Path(file_path).name,
            "read": handle.read,
            "readline": handle.readline,
            "readlines": handle.readlines,
            "write": handle.write,
            "writelines": handle.writelines,
            "close": handle.close,
            "flush": handle.flush,
            "seek": handle.seek,
            "tell": handle.tell,
            "truncate": handle.truncate,
            "isatty": handle.isatty,
        }
        if mode.startswith("r") and Path(file_path).exists():
            handle.seek(0)
        return methods

    def _builtin_read_file(self, path, encoding: str = "utf-8"):
        return Path(str(path)).read_text(encoding=encoding)

    def _builtin_write_file(self, path, content, encoding: str = "utf-8"):
        Path(str(path)).write_text(str(content), encoding=encoding)
        return str(path)

    def _builtin_append_file(self, path, content, encoding: str = "utf-8"):
        with open(str(path), "a", encoding=encoding) as file:
            file.write(str(content))
        return str(path)

    def _builtin_import(self, name):
        return self.import_module(name)

    def _builtin_fetch(self, url, destination=None):
        if url is None:
            raise ValueError("fetch requires a URL")
        url = str(url)
        if destination is None:
            parsed = urlparse(url)
            suffix = Path(parsed.path).suffix or ".bin"
            handle = tempfile.NamedTemporaryFile(prefix="sodium-fetch-", suffix=suffix, delete=False)
            handle.close()
            destination = handle.name
        else:
            destination = str(destination)
            path = Path(destination)
            if not path.parent.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(url, destination)
        return str(destination)

    def _builtin_import_python(self, name):
        return self.import_python(name)

    def _builtin_await(self, value=None):
        return value

    def _builtin_on(self, trigger_name, *args, **kwargs):
        if trigger_name is None:
            raise TypeError("on() requires a trigger name")

        params: list[str] = []
        defaults: dict[str, object] = {}
        for name, value in kwargs.items():
            params.append(str(name))
            defaults[str(name)] = value

        for value in args:
            if isinstance(value, str):
                params.append(value)
                defaults[value] = None
                continue
            if isinstance(value, dict) and value.get("type") == "identifier":
                name = value.get("value")
                if isinstance(name, str):
                    params.append(name)
                    defaults[name] = None
                    continue
            raise TypeError("on() parameters must be named arguments")

        func = {
            "kind": "function",
            "name": None,
            "params": params,
            "defaults": defaults,
            "body": [],
            "scope": list(self.scopes),
            "trigger_name": str(trigger_name),
            "source": self.filename if self.filename not in {"<source>", ""} else "<source>",
            "content": self.content,
        }
        self._register_trigger_handler(func)
        return func

    def _builtin_trigger(self, trigger_name, *args, **kwargs):
        if trigger_name is None:
            raise TypeError("trigger() requires a trigger name")

        trigger_name = str(trigger_name)
        if trigger_name == "end":
            self._end_trigger_fired = True
        if trigger_name in self._trigger_dispatching:
            return []
        self._discover_trigger_handlers()
        results: list[object] = []
        self._trigger_dispatching.add(trigger_name)
        try:
            for handler in self.__class__._trigger_registry.get(trigger_name, []):
                if not isinstance(handler, dict) or handler.get("kind") != "function":
                    continue
                results.append(self.invoke_function(handler, list(args), {}, dict(kwargs)))
            return results
        finally:
            self._trigger_dispatching.discard(trigger_name)

    def _discover_trigger_handlers(self):
        for root in self._trigger_scan_roots():
            resolved = str(root.resolve())
            if resolved in self.__class__._trigger_scan_roots_done:
                continue
            self.__class__._trigger_scan_roots_done.add(resolved)
            for file_path in self._iter_trigger_files(root):
                source = file_path.read_text(encoding="utf-8")
                tokens = self.sodium.res.Tokenizer(self.sodium, self.options).run(source)
                for handler in self._handlers_from_tokens(tokens, source, str(file_path)):
                    self._register_trigger_handler(handler)

    def _trigger_scan_roots(self):
        roots: list[Path] = []
        filename_value = self.filename or ""
        if filename_value not in {"<source>", ""}:
            filename_path = Path(filename_value)
            if filename_path.exists() and filename_path.is_file():
                file_dir = filename_path.resolve().parent
                if file_dir.exists() and file_dir.is_dir() and not self._directory_is_too_large(file_dir):
                    roots.append(file_dir)
        ordered: list[Path] = []
        seen: set[str] = set()
        for root in roots:
            resolved = str(root.resolve())
            if resolved not in seen:
                seen.add(resolved)
                ordered.append(root)
        return ordered

    def _directory_is_too_large(self, root: Path, threshold: int = 2000) -> bool:
        count = 0
        ignored_dirs = {".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules", ".idea", ".vscode"}
        stack = [root]
        seen_dirs: set[str] = set()
        try:
            while stack:
                current = stack.pop()
                with os.scandir(current) as entries:
                    for entry in entries:
                        if entry.name in ignored_dirs:
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            resolved = str(Path(entry.path).resolve())
                            if resolved not in seen_dirs:
                                seen_dirs.add(resolved)
                                stack.append(Path(entry.path))
                            continue
                        if entry.is_file(follow_symlinks=False):
                            count += 1
                            if count > threshold:
                                return True
        except OSError:
            return True
        return False

    def _iter_trigger_files(self, root: Path):
        if not root.exists() or not root.is_dir() or self._directory_is_too_large(root):
            return []

        ignored_dirs = {".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules", ".idea", ".vscode"}
        matches: list[Path] = []
        stack = [root]
        seen_dirs: set[str] = set()

        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        if entry.name in ignored_dirs:
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            resolved = str(Path(entry.path).resolve())
                            if resolved not in seen_dirs:
                                seen_dirs.add(resolved)
                                stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False) and entry.name.endswith(".na"):
                            matches.append(Path(entry.path))
            except OSError:
                continue

        return matches

    def _register_trigger_handler(self, func: dict):
        trigger_name = func.get("trigger_name")
        if trigger_name is None:
            return
        registry = self.__class__._trigger_registry.setdefault(str(trigger_name), [])
        signature = self._trigger_signature(func)
        for existing in registry:
            if self._trigger_signature(existing) == signature:
                return
        registry.append(func)

    def _trigger_signature(self, func: dict):
        return (
            str(func.get("trigger_name")),
            tuple(func.get("params", [])),
            tuple((name, repr(value)) for name, value in func.get("defaults", {}).items()),
            str(func.get("source", "")),
        )

    def _handlers_from_tokens(self, tokens: list[list[dict]], source: str, filename: str):
        handlers: list[dict] = []

        def visit(node):
            if not isinstance(node, dict):
                return
            if node.get("type") == "handler":
                children = node.get("children", [])
                if len(children) < 2:
                    return
                target = children[0]
                if not self._is_on_target(target):
                    return
                trigger_name = self._trigger_name_from_call(target)
                if trigger_name is None:
                    return
                params: list[str] = []
                defaults: dict[str, object] = {}
                for arg in target.get("children", [])[1:]:
                    if isinstance(arg, dict) and arg.get("type") == "assignment":
                        left, right = arg.get("children", [None, None])
                        if isinstance(left, dict) and left.get("type") == "identifier":
                            params.append(left["value"])
                            defaults[left["value"]] = right
                        continue
                    if isinstance(arg, dict) and arg.get("type") == "identifier":
                        params.append(arg["value"])
                        defaults[arg["value"]] = None
                        continue
                handlers.append({
                    "kind": "function",
                    "name": None,
                    "params": params,
                    "defaults": defaults,
                    "body": children[1:],
                    "scope": [self.builtins_for_file(filename), {}],
                    "trigger_name": trigger_name,
                    "source": filename,
                    "content": source,
                })
            for child in node.get("children", []):
                if isinstance(child, dict):
                    visit(child)

        for line in tokens:
            for token in line:
                visit(token)
        return handlers

    def _is_on_target(self, target):
        if not isinstance(target, dict) or target.get("type") != "call":
            return False
        children = target.get("children", [])
        if not children:
            return False
        callee = children[0]
        return isinstance(callee, dict) and callee.get("type") == "identifier" and callee.get("value") == "on"

    def _trigger_name_from_call(self, target):
        if not self._is_on_target(target):
            return None
        children = target.get("children", [])
        if len(children) < 2:
            return None
        first = children[1]
        if isinstance(first, dict):
            if first.get("type") == "string":
                return first.get("value")
            if first.get("type") == "literal":
                return first.get("value")
            if first.get("type") == "identifier":
                return first.get("value")
        return None

    def _module_search_roots(self):
        roots = []
        if self.filename not in {"<source>", ""}:
            file_root = Path(self.filename).resolve().parent
            roots.append(file_root)
            roots.append(file_root / "resources" / "libs")
            roots.append(file_root / "sodium" / "resources" / "libs")
        roots.append(Path.cwd())
        roots.append(Path.cwd() / "resources" / "libs")
        roots.append(Path.cwd() / "sodium" / "resources" / "libs")
        roots.append(Path(__file__).resolve().parent / "libs")
        seen = set()
        ordered = []
        for root in roots:
            resolved = str(Path(root).resolve())
            if resolved not in seen:
                seen.add(resolved)
                ordered.append(Path(root))
        return ordered

    def import_module(self, module_name, token: dict | None = None):
        if isinstance(module_name, str):
            name = module_name.strip()
        else:
            name = str(module_name)
        if not name:
            self.fail(token, "import requires a module name", ImportError)

        direct_path = None
        if not name.startswith(("http://", "https://")):
            candidate = Path(name).expanduser()
            if not candidate.is_absolute():
                candidate = (Path(self.filename).resolve().parent / candidate) if self.filename not in {"<source>", ""} else (Path.cwd() / candidate)
            if candidate.exists() and candidate.is_file():
                direct_path = candidate.resolve()

        if direct_path is not None:
            resolved = str(direct_path)
            if resolved in self.__class__._module_cache:
                imported = self.__class__._module_cache[resolved]
                if isinstance(imported, dict):
                    self.scopes[-1].update(imported)
                return imported
            source = direct_path.read_text()
            tokenizer = self.sodium.res.Tokenizer(self.sodium, self.options)
            previous_filename = self.filename
            previous_scopes = list(self.scopes)
            module_interpreter = self.__class__(self.sodium, self.options)
            module_interpreter.filename = str(direct_path)
            module_interpreter.content = source
            try:
                tokens = tokenizer.run(source)
                module_interpreter.scopes = [module_interpreter.builtins(), {}]
                module_result = module_interpreter.lines(tokens)
                if isinstance(module_result, tuple) and module_result[0] == "return":
                    module_result = module_result[1]
                imported = module_interpreter.scopes[-1]
                self.__class__._module_cache[resolved] = imported
                if isinstance(imported, dict):
                    self.scopes[-1].update(imported)
                return imported
            finally:
                self.filename = previous_filename
                self.scopes = previous_scopes

        if name.endswith(".na"):
            name = name[:-3]
        candidate_names = [name, name.replace(".", "/")]
        for root in self._module_search_roots():
            for candidate in candidate_names:
                path = (root / candidate).with_suffix(".na")
                if path.exists():
                    break
                path = root / f"{candidate}.na"
                if path.exists():
                    break
            else:
                continue
            resolved = str(path.resolve())
            if resolved in self.__class__._module_cache:
                imported = self.__class__._module_cache[resolved]
                if isinstance(imported, dict):
                    self.scopes[-1].update(imported)
                return imported
            source = path.read_text()
            tokenizer = self.sodium.res.Tokenizer(self.sodium, self.options)
            previous_filename = self.filename
            previous_scopes = list(self.scopes)
            module_interpreter = self.__class__(self.sodium, self.options)
            module_interpreter.filename = str(path)
            module_interpreter.content = source
            try:
                tokens = tokenizer.run(source)
                module_interpreter.scopes = [module_interpreter.builtins(), {}]
                module_result = module_interpreter.lines(tokens)
                if isinstance(module_result, tuple) and module_result[0] == "return":
                    module_result = module_result[1]
                imported = module_interpreter.scopes[-1]
                self.__class__._module_cache[resolved] = imported
                if isinstance(imported, dict):
                    self.scopes[-1].update(imported)
                return imported
            finally:
                self.filename = previous_filename
                self.scopes = previous_scopes
        raise ImportError(f"cannot import {name!r}: module not found")

    def import_python(self, module_name, token: dict | None = None):
        if isinstance(module_name, str):
            name = module_name.strip()
        else:
            name = str(module_name)
        if not name:
            self.fail(token, "import_python requires a module name", ImportError)
        parts = [part for part in name.split(".") if part]
        if not parts:
            self.fail(token, "import_python requires a module name", ImportError)
        try:
            full_name = ".".join(parts)
            obj = importlib.import_module(full_name)

            if hasattr(obj, "__path__"):
                return obj

            parent = obj
            for part in reversed(parts[1:]):
                parent = getattr(parent, part, None)
                if parent is None:
                    break
            if parent is not None and parent is not obj:
                return parent
            return obj
        except (AttributeError, ImportError, ModuleNotFoundError) as error:
            raise ImportError(f"cannot import python module {name!r}: {error}") from error

    def lines(self, lines: list[list[dict]]):
        result = None
        for line in lines:
            for token in line:
                result = self.value(token)
                if isinstance(result, tuple) and result[0] in {"return", "break", "continue"}:
                    return result
        return result

    def value(self, token: dict | None):
        if token is None:
            return None
        self.mark(token)
        kind = token["type"]
        value = token["value"]
        children = token.get("children", [])

        if kind in {"number", "bytes", "literal"}:
            return value
        if kind == "string":
            template = token.get("template")
            if template is not None:
                rendered = ""
                for piece in template["parts"]:
                    if isinstance(piece, str):
                        rendered += piece
                    else:
                        rendered += self._fmt_value(self.value(piece))
                return rendered
            return value
        if kind == "identifier":
            return self.lookup(token)
        if kind == "array":
            values = []
            for child in children:
                if isinstance(child, dict) and child.get("type") == "unary" and child.get("value") == "*":
                    expanded = self.value(child["children"][0])
                    if isinstance(expanded, dict):
                        values.extend(expanded)
                    elif isinstance(expanded, (list, tuple, set)):
                        values.extend(expanded)
                    else:
                        values.append(expanded)
                    continue
                values.append(self.value(child))
            return values
        if kind == "set":
            return {self.value(child) for child in children}
        if kind == "map":
            result = {}
            for pair in children:
                if not isinstance(pair, dict):
                    continue
                key_token = pair.get("children", [None, None])[0]
                if isinstance(key_token, dict) and key_token.get("type") == "identifier":
                    key = key_token.get("value")
                else:
                    key = self.value(key_token)
                result[key] = self.value(pair.get("children", [None, None])[1])
            return result
        if kind == "custom":
            result = {}
            for pair in children:
                if not isinstance(pair, dict):
                    continue
                key_token = pair.get("children", [None, None])[0]
                if isinstance(key_token, dict) and key_token.get("type") == "identifier":
                    key = key_token.get("value")
                else:
                    key = self.value(key_token)
                result[key] = self.value(pair.get("children", [None, None])[1])
            return result
        if kind == "member":
            return self.member(self.value(children[0]), value, token)
        if kind == "instance_member":
            return self.instance_member(self.value(children[0]), value, token)
        if kind == "index":
            return self.index(self.value(children[0]), self.value(children[1]), token)
        if kind == "slice":
            obj = self.value(children[0])
            start = self.value(children[1]) if len(children) > 1 and children[1] is not None else None
            end = self.value(children[2]) if len(children) > 2 and children[2] is not None else None
            if start is None and end is None:
                return obj[:]
            if start is None:
                return obj[:end]
            if end is None:
                return obj[start:]
            return obj[start:end]
        if kind == "unary":
            operand = self.value(children[0])
            if value == "+":
                return +operand
            if value == "-":
                return -operand
            if value == "*":
                if isinstance(operand, dict):
                    return dict(operand)
                if isinstance(operand, (list, tuple, set)):
                    return list(operand)
                return [operand]
            if value == "not":
                return not operand
            self.fail(token, f"unknown unary operator {value!r}", SyntaxError)
        if kind == "percent":
            return self.value(children[0]) / 100
        if kind == "operator":
            left = self.value(children[0])
            right = self.value(children[1])
            if value == "and":
                return left and right
            if value == "or":
                return left or right
            return self.binary(value, left, right, token)
        if kind == "assignment":
            result = self.value(children[1])
            self.assign(children[0], result)
            return result
        if kind == "from":
            source = self.value(children[0])
            for target in children[1:]:
                if not isinstance(target, dict) or target.get("type") != "identifier":
                    raise SyntaxError("from bindings require identifiers on the left-hand side")
                value = self.member(source, target["value"], target)
                self.assign(target, value)
            return source
        if kind == "call":
            return self.call(token)
        if kind == "while":
            return self.execute_while(token)
        if kind == "for":
            return self.execute_for(token)
        if kind == "handler":
            return self.handler(token)
        if kind == "match":
            subject = self.value(children[0])
            for case in children[1:]:
                pattern = case.get("children", [None, None])[0]
                body = case.get("children", [None, []])[1]
                if pattern is None:
                    continue
                if isinstance(pattern, dict) and pattern.get("type") == "identifier" and pattern.get("value") == "_":
                    return self.lines(body)
                match_value = self.value(pattern)
                if subject == match_value:
                    return self.lines(body)
            return None
        if kind == "import":
            return self.import_module(self.value(children[0]), token)
        if kind == "return":
            if children:
                return ("return", self.value(children[0]))
            return ("return", None)
        if kind == "break":
            return ("break", None)
        if kind == "continue":
            return ("continue", None)
        self.fail(token, f"unsupported token {kind!r}", SyntaxError)

    def lookup(self, token: dict):
        name = token["value"]
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        self.fail(token, f"undefined name {name!r}", NameError)

    def assign(self, target: dict, value):
        if target["type"] == "identifier":
            if isinstance(value, dict) and value.get("kind") in {"function", "class"}:
                value["name"] = target["value"]
            for scope in reversed(self.scopes):
                if target["value"] in scope:
                    scope[target["value"]] = value
                    return
            self.scopes[-1][target["value"]] = value
            return
        if target["type"] == "member":
            obj = self.value(target["children"][0])
            name = target["value"]
            if isinstance(obj, dict):
                obj[name] = value
                return
            setattr(obj, name, value)
            return
        if target["type"] == "index":
            obj = self.value(target["children"][0])
            key = self.value(target["children"][1])
            obj[key] = value
            return
        self.fail(target, "assignment target must be a name, member, or index", SyntaxError)

    def binary(self, operator: str, left, right, token: dict):
        try:
            if operator == "+":
                return left + right
            if operator == "-":
                return left - right
            if operator == "*":
                return left * right
            if operator == "/":
                return left / right
            if operator == "//":
                return left // right
            if operator == "%":
                return left % right
            if operator in {"^", "**"}:
                return left ** right
            if operator == "==":
                return left == right
            if operator == "!=":
                return left != right
            if operator == ">":
                return left > right
            if operator == "<":
                return left < right
            if operator == ">=":
                return left >= right
            if operator == "<=":
                return left <= right
            if operator == "in":
                return left in right
            if operator == "not in":
                return left not in right
            if operator == "is":
                return self.type_matches(left, right)
            if operator == "is not":
                return not self.type_matches(left, right)
        except (ArithmeticError, TypeError, ValueError) as error:
            self.fail(token, str(error), type(error))
        self.fail(token, f"unsupported operator {operator!r}", SyntaxError)

    def public_type_name(self, value):
        if value is True or value is False:
            return "boolean"
        if value is None:
            return "null"
        if isinstance(value, dict):
            if value.get("kind") == "function":
                return "function"
            if value.get("kind") == "class":
                return "class"
            if "__class__" in value:
                class_def = value.get("__class__")
                if isinstance(class_def, dict):
                    return class_def.get("name") or "instance"
                return "instance"
            return "map"
        if isinstance(value, (int, float)):
            return "number"
        if isinstance(value, str):
            return "string"
        if isinstance(value, list):
            return "array"
        if isinstance(value, set):
            return "set"
        if isinstance(value, tuple):
            return "array"
        if isinstance(value, bytes):
            return "bytes"
        if isinstance(value, type):
            return value.__name__
        if callable(value):
            return "function"
        if hasattr(value, "__class__"):
            python_name = type(value).__name__
            return {
                "str": "string",
                "int": "number",
                "float": "number",
                "bool": "boolean",
                "list": "array",
                "tuple": "array",
                "dict": "map",
                "set": "set",
                "bytes": "bytes",
            }.get(python_name, "value")
        return "value"

    def runtime_type(self, value):
        if value is True or value is False:
            return "boolean"
        if value is None:
            return "null"
        if isinstance(value, dict):
            if value.get("kind") == "function":
                return "function"
            if value.get("kind") == "class":
                return "class"
            if "__class__" in value:
                return "instance"
            return "map"
        if isinstance(value, (int, float)):
            return "number"
        if isinstance(value, str):
            return "string"
        if isinstance(value, list):
            return "array"
        if isinstance(value, set):
            return "set"
        if isinstance(value, tuple):
            return "array"
        if isinstance(value, type):
            return "class"
        if callable(value):
            return "function"
        if hasattr(value, "__class__"):
            module_name = type(value).__module__
            if module_name == "builtins":
                return self.public_type_name(value)
            return "value"
        return "value"

    def type_matches(self, left, right):
        left_kind = self.runtime_type(left)

        if right is None:
            return left_kind == "null"
        if right is True or right is False:
            return left_kind == "boolean"
        if right is left:
            return True

        if isinstance(right, dict):
            if right.get("kind") == "function":
                return left_kind == "function"
            if right.get("kind") == "class":
                if left_kind == "class":
                    return True
                if left_kind == "instance":
                    return isinstance(left, dict) and left.get("__class__") is right
                return False
            return False

        if isinstance(right, str):
            return left_kind == right

        if isinstance(right, type):
            if left_kind == "class":
                return left is right or left.__name__ == right.__name__
            if left_kind == "instance":
                return isinstance(left, dict) and left.get("__class__") is right
            return isinstance(left, right)

        if callable(right):
            name = getattr(right, "__name__", "")
            if name.startswith("_builtin_"):
                name = name[len("_builtin_") :]
            if name in {"number", "string", "boolean", "array", "map", "set", "bytes", "function", "class", "null"}:
                if name == "class":
                    return left_kind == "class"
                if name == "function":
                    return left_kind == "function"
                return left_kind == name
            return left is right or (callable(left) and getattr(left, "__name__", "") == name)

        return isinstance(left, type(right)) if right is not None and not isinstance(right, (dict, list, set, tuple)) else False

    def resolve_class_member(self, class_def: dict, name: str):
        if not isinstance(class_def, dict):
            return None
        seen = set()
        queue = [class_def]
        while queue:
            current = queue.pop(0)
            if id(current) in seen:
                continue
            seen.add(id(current))
            if name in current.get("members", {}):
                return current["members"][name]
            if name in current.get("methods", {}):
                return current["methods"][name]
            if name in current:
                return current[name]
            queue.extend(current.get("bases", []))
        return None

    def _builtin_type_members(self, obj):
        if isinstance(obj, type):
            return [obj]
        function = getattr(obj, "__func__", obj)
        name = getattr(function, "__name__", "")
        type_map = {
            "_builtin_string": [str],
            "_builtin_number": [int, float],
            "_builtin_boolean": [bool],
            "_builtin_array": [list],
            "_builtin_map": [dict],
            "_builtin_set": [set],
            "_builtin_bytes": [bytes],
        }
        return type_map.get(name, [])

    def member(self, obj, name: str, token: dict):
        if isinstance(obj, dict):
            if obj.get("kind") == "super":
                instance = obj.get("instance")
                current_class = obj.get("class") or (instance.get("__class__") if isinstance(instance, dict) else None)
                found = self.resolve_super_member(current_class, name)
                if found is not None:
                    if isinstance(found, dict) and found.get("kind") == "function":
                        return {"kind": "bound_method", "method": found, "instance": instance, "class": current_class}
                    return found
                label = (current_class or {}).get("name", "super") if isinstance(current_class, dict) else "super"
                self.fail(token, f"'{label}' has no member {name!r}", AttributeError)
            if obj.get("kind") == "class":
                found = self.resolve_class_member(obj, name)
                if found is not None:
                    return found
                label = obj.get("name") or "class"
                self.fail(token, f"'{label}' has no member {name!r}", AttributeError)
            if name in obj:
                return obj[name]
            if "__class__" in obj:
                subject = obj.get("__class__", obj)
                label = getattr(subject, "__name__", None) or getattr(subject, "name", None) or self.public_type_name(subject)
                self.fail(token, f"'{label}' is an object; use ':' for object members", AttributeError)
            label = self.public_type_name(obj)
            self.fail(token, f"'{label}' has no member {name!r}", AttributeError)

        for candidate in self._builtin_type_members(obj):
            if hasattr(candidate, name):
                return getattr(candidate, name)

        if isinstance(obj, type):
            if hasattr(obj, name):
                return getattr(obj, name)
            label = getattr(obj, "__name__", None) or self.public_type_name(obj)
            self.fail(token, f"'{label}' has no member {name!r}", AttributeError)

        module_name = type(obj).__name__ if not isinstance(obj, dict) else ""
        if module_name == "module":
            if hasattr(obj, name):
                return getattr(obj, name)
            label = getattr(type(obj), "__name__", None) or self.public_type_name(obj)
            self.fail(token, f"'{label}' has no member {name!r}", AttributeError)

        if hasattr(obj, "__class__") and not self._builtin_type_members(obj):
            subject = obj.get("__class__", obj) if isinstance(obj, dict) else obj
            label = getattr(subject, "__name__", None) or getattr(subject, "name", None) or self.public_type_name(subject)
            self.fail(token, f"'{label}' is an object; use ':' for object members", AttributeError)

        if hasattr(obj, name):
            return getattr(obj, name)

        subject = obj.get("__class__", obj) if isinstance(obj, dict) else obj
        label = getattr(subject, "__name__", None) or getattr(subject, "name", None) or self.public_type_name(subject)
        self.fail(token, f"'{label}' has no member {name!r}", AttributeError)

    def instance_member(self, obj, name: str, token: dict):
        if isinstance(obj, dict):
            if obj.get("kind") == "super":
                return self.member(obj, name, token)
            if name in obj:
                return obj[name]
            if "__class__" in obj:
                class_def = obj["__class__"]
                found = self.resolve_class_member(class_def, name)
                if found is not None:
                    if isinstance(found, dict) and found.get("kind") == "function":
                        return {"kind": "bound_method", "method": found, "instance": obj, "class": class_def}
                    return found
        if hasattr(obj, name):
            return getattr(obj, name)
        subject = obj.get("__class__", obj) if isinstance(obj, dict) else obj
        label = getattr(subject, "__name__", None) or getattr(subject, "name", None) or self.public_type_name(subject)
        self.fail(token, f"'{label}' has no member {name!r}", AttributeError)

    def resolve_super_member(self, class_def: dict, name: str):
        if not isinstance(class_def, dict):
            return None
        seen = set()
        queue = list(class_def.get("bases", []))
        while queue:
            current = queue.pop(0)
            if not isinstance(current, dict) or current.get("kind") != "class":
                continue
            if id(current) in seen:
                continue
            seen.add(id(current))
            if name in current.get("members", {}):
                return current["members"][name]
            if name in current.get("methods", {}):
                return current["methods"][name]
            if name in current:
                return current[name]
            queue.extend(current.get("bases", []))
        return None

    def index(self, obj, key, token: dict):
        try:
            return obj[key]
        except (IndexError, KeyError, TypeError) as error:
            self.fail(token, str(error), type(error))

    def execute_while(self, token: dict):
        condition, body = token["children"]
        last = None
        while bool(self.value(condition)):
            result = self.lines(body)
            if isinstance(result, tuple):
                if result[0] == "return":
                    return result[1]
                if result[0] == "break":
                    break
                if result[0] == "continue":
                    continue
            last = result
        return last

    def execute_for(self, token: dict):
        target, iterable, body = token["children"]
        source = self.value(iterable)
        last = None
        if isinstance(source, dict):
            sequence = list(source.keys())
        elif isinstance(source, (list, tuple, set, range)):
            sequence = list(source)
        elif isinstance(source, str):
            sequence = list(source)
        else:
            try:
                sequence = list(source)
            except TypeError as error:
                self.fail(token, f"for loop requires an iterable value: {error}", TypeError)

        for item in sequence:
            self.assign(target, item)
            result = self.lines(body)
            if isinstance(result, tuple):
                if result[0] == "return":
                    return result[1]
                if result[0] == "break":
                    break
                if result[0] == "continue":
                    continue
            last = result
        return last

    def call(self, token: dict):
        callee, *arguments = token["children"]
        target = self.value(callee)
        args: list[object] = []
        kwargs: dict[str, object] = {}

        for argument in arguments:
            if isinstance(argument, dict) and argument.get("type") == "assignment":
                left, right = argument.get("children", [None, None])
                if isinstance(left, dict) and left.get("type") == "identifier" and right is not None:
                    kwargs[left["value"]] = self.value(right)
                    continue
            if isinstance(argument, dict) and argument.get("type") == "unary" and argument.get("value") == "*":
                expanded = self.value(argument["children"][0])
                if isinstance(expanded, dict):
                    kwargs.update(dict(expanded))
                    continue
                if isinstance(expanded, (list, tuple, set)):
                    args.extend(expanded)
                    continue
                self.fail(argument, "starred value must be an array, set, tuple, or map", TypeError)
            args.append(self.value(argument))

        callee_name = "<anonymous>"
        if isinstance(callee, dict):
            if callee.get("type") == "identifier":
                callee_name = str(callee.get("value", callee_name))
            elif callee.get("type") == "member":
                member_name = callee.get("value")
                if isinstance(member_name, str):
                    callee_name = member_name
        elif hasattr(target, "__name__") and target.__name__:
            callee_name = str(target.__name__)
            if callee_name.startswith("_builtin_"):
                callee_name = callee_name[len("_builtin_") :]

        if isinstance(target, dict):
            kind = target.get("kind")
            if kind == "function":
                return self.invoke_function(target, args, token, kwargs)
            if kind == "condition":
                if not args and not kwargs:
                    return target.get("value", False)
                if kwargs:
                    return bool(next(iter(kwargs.values())))
                return bool(args[0])
            if kind == "return":
                if args:
                    return ("return", self.value(args[0]))
                return ("return", None)
            if kind == "break":
                return ("break", None)
            if kind == "continue":
                return ("continue", None)
            if kind == "class":
                if target.get("name") == "class":
                    bases = []
                    if args:
                        if not all(isinstance(arg, dict) and arg.get("kind") == "class" for arg in args):
                            self.fail(token, "class constructor accepts only base classes", TypeError)
                        bases = list(args)
                    return {"kind": "class", "name": None, "bases": bases, "methods": {}, "members": {}, "body": [], "scope": list(self.scopes)}
                return self.instantiate_class(target, args, token, kwargs)
            if kind == "super_factory":
                instance = target.get("instance")
                class_def = target.get("class")
                if instance is None or class_def is None:
                    for scope in reversed(self.scopes):
                        if "self" in scope:
                            instance = scope["self"]
                            class_def = instance.get("__class__") if isinstance(instance, dict) else None
                            if instance is not None and class_def is not None:
                                break
                return {"kind": "super", "instance": instance, "class": class_def}
            if kind == "bound_method":
                return self.invoke_function(target["method"], [target["instance"], *args], token, kwargs)
            if "__class__" in target:
                method = target.get("call")
                if isinstance(method, dict):
                    return self.invoke_function(method, [target, *args], token, kwargs)
                if callable(method):
                    try:
                        return method(target, *args, **kwargs)
                    except (TypeError, ValueError, KeyError, IndexError, AttributeError) as error:
                        self.fail(token, str(error), type(error))

        if callable(target):
            frame = {
                "name": callee_name,
                "source": self.filename or "<source>",
                "position": token.get("position", self.error_position),
                "end": token.get("end", self.error_end),
                "content": self.content,
            }
            self.call_stack.append(frame)
            try:
                return target(*args, **kwargs)
            except Exception as error:
                if isinstance(error, SodiumError):
                    raise
                self.fail(token, str(error), type(error))
            finally:
                if self.call_stack and self.call_stack[-1] == frame:
                    self.call_stack.pop()
        self.fail(callee, "value is not callable", TypeError)

    def invoke_function(self, func: dict, args: list, token: dict, kwargs: dict | None = None):
        params = list(func.get("params", []))
        defaults = dict(func.get("defaults", {}))
        body = list(func.get("body", []))
        kwargs = dict(kwargs or {})
        remaining_args = list(args)
        bound: dict[str, object] = {}

        previous_filename = self.filename
        previous_content = self.content
        source = func.get("source") or self.filename
        content = func.get("content") or self.content
        self.filename = source
        self.content = content

        variadic_positional_name = next((name for name in params if isinstance(defaults.get(name), dict) and defaults[name].get("type") == "string" and defaults[name].get("value") == "*"), None)
        variadic_keyword_name = next((name for name in params if isinstance(defaults.get(name), dict) and defaults[name].get("type") == "string" and defaults[name].get("value") == "**"), None)

        for name in params:
            default = defaults.get(name)
            default_value = None
            if isinstance(default, dict):
                default_value = default.get("value") if not isinstance(default, dict) or "type" in default else None
                if default.get("type") in {"string", "number", "bytes", "literal", "identifier", "array", "map", "set", "custom", "call", "member", "index", "slice", "assignment", "operator", "unary", "percent", "handler", "match", "from", "return", "import"}:
                    default_value = default.get("value")
            is_variadic_positional = default_value == "*"
            is_variadic_keyword = default_value == "**"

            if is_variadic_positional:
                bound[name] = list(remaining_args)
                remaining_args = []
                continue
            if is_variadic_keyword:
                bound[name] = dict(kwargs)
                kwargs = {}
                continue
            if name in kwargs:
                bound[name] = kwargs.pop(name)
                continue
            if remaining_args:
                bound[name] = remaining_args.pop(0)
                continue
            if default is not None and not is_variadic_positional and not is_variadic_keyword:
                if isinstance(default, dict) and any(key in default for key in ("type", "children", "value")) and default.get("type") in {"string", "number", "bytes", "literal", "identifier", "array", "map", "set", "custom", "call", "member", "index", "slice", "assignment", "operator", "unary", "percent", "handler", "match", "from", "return", "import"}:
                    bound[name] = self.value(default)
                else:
                    bound[name] = default
                continue
            self.fail(token, f"missing required argument(s): {name}", TypeError)

        if remaining_args and variadic_positional_name is None:
            self.fail(token, f"expected {len(params)} arguments, got {len(args) + len(kwargs)}", TypeError)
        if kwargs and variadic_keyword_name is None:
            unexpected = ", ".join(repr(name) for name in kwargs)
            self.fail(token, f"unexpected keyword argument(s): {unexpected}", TypeError)
        if not body and not params and not args and not kwargs:
            return {"kind": "function", "params": [], "defaults": {}, "body": [], "scope": list(self.scopes)}

        current_class = func.get("owner")
        previous = self.scopes
        scope = dict((name, bound[name]) for name in params)
        if "self" in scope:
            scope["super"] = {
                "kind": "super_factory",
                "instance": scope["self"],
                "class": current_class or (scope["self"].get("__class__") if isinstance(scope["self"], dict) else None),
            }
        func_scope = list(func.get("scope", self.scopes))
        if func_scope and isinstance(func_scope[0], dict):
            file_path, dir_path = self._resolve_file_context(source)
            func_scope[0]["_this"] = file_path
            func_scope[0]["_here"] = dir_path
        self.scopes = func_scope + [scope]
        frame = {
            "name": func.get("name") or func.get("__name__") or "<anonymous>",
            "source": func.get("source") or self.filename or "<source>",
            "position": token.get("position", self.error_position) if isinstance(token, dict) else self.error_position,
            "end": token.get("end", self.error_end) if isinstance(token, dict) else self.error_end,
            "content": func.get("content") or self.content or "",
            "trigger_name": func.get("trigger_name"),
        }
        self.call_stack.append(frame)
        try:
            try:
                result = self.lines(body)
            except Exception as error:
                if isinstance(error, SodiumError):
                    raise
                self.fail(token, str(error), type(error))
            if isinstance(result, tuple) and result[0] == "return":
                return result[1]
            return result
        finally:
            self.scopes = previous
            self.filename = previous_filename
            self.content = previous_content
            if self.call_stack and self.call_stack[-1] == frame:
                self.call_stack.pop()

    def instantiate_class(self, class_def: dict, args: list, token: dict, kwargs: dict | None = None):
        instance = {"__class__": class_def}
        for name, value in class_def.get("members", {}).items():
            instance[name] = value
        for name, method in class_def.get("methods", {}).items():
            instance[name] = method
        for base in class_def.get("bases", []):
            if not isinstance(base, dict):
                continue
            for name, value in base.get("members", {}).items():
                instance.setdefault(name, value)
            for name, method in base.get("methods", {}).items():
                instance.setdefault(name, method)
        init = self.resolve_class_member(class_def, "init")
        kwargs = dict(kwargs or {})
        if isinstance(init, dict) and init.get("kind") == "function":
            self.invoke_function(init, [instance, *args], token, kwargs)
        elif init is not None and callable(init):
            self.invoke_function(init, [instance, *args], token, kwargs)
        elif args or kwargs:
            self.fail(token, "class has no initializer", TypeError)
        return instance

    def handler(self, token: dict):
        target, *body = token["children"]

        if isinstance(target, dict):
            if target.get("type") == "identifier":
                callee_name = target.get("value")
                if callee_name == "function":
                    return {"kind": "function", "name": None, "params": [], "defaults": {}, "body": body, "scope": list(self.scopes)}
                if callee_name == "class":
                    class_def = {"kind": "class", "name": None, "bases": [], "methods": {}, "members": {}, "body": body, "scope": list(self.scopes)}
                    scope = {}
                    previous = self.scopes
                    self.scopes = list(self.scopes) + [scope]
                    try:
                        for line in body:
                            for statement in line:
                                result = self.value(statement)
                                if isinstance(statement, dict) and statement.get("type") == "assignment":
                                    left = statement["children"][0]
                                    if isinstance(left, dict) and left.get("type") == "identifier":
                                        name = left["value"]
                                        scope[name] = result
                                        class_def["members"][name] = result
                                        class_def[name] = result
                                        if isinstance(result, dict) and result.get("kind") in {"function", "class", "condition"}:
                                            result["owner"] = class_def
                                            class_def["methods"][name] = result
                        return class_def
                    finally:
                        self.scopes = previous
            if target.get("type") == "call":
                callee = target.get("children", [None])[0]
                callee_value = self.value(callee) if isinstance(callee, dict) else None
                if isinstance(callee_value, dict) and callee_value.get("name") == "while":
                    condition = target.get("children", [None, None])[1] if len(target.get("children", [])) > 1 else None
                    if condition is None:
                        self.fail(token, "while() requires a condition", TypeError)
                    last = None
                    while bool(self.value(condition)):
                        result = self.lines(body)
                        if isinstance(result, tuple):
                            if result[0] == "return":
                                return result[1]
                            if result[0] == "break":
                                break
                            if result[0] == "continue":
                                continue
                        last = result
                    return last
                if isinstance(callee_value, dict) and callee_value.get("name") == "for":
                    if len(target.get("children", [])) < 2:
                        self.fail(token, "for() requires a target and iterable", TypeError)
                    expr = target.get("children", [None, None])[1]
                    if not isinstance(expr, dict) or expr.get("type") != "operator" or expr.get("value") != "in":
                        self.fail(token, "for() requires a target in iterable expression", TypeError)
                    target_var, iterable = expr.get("children", [None, None])
                    source = self.value(iterable)
                    last = None
                    if isinstance(source, dict):
                        sequence = list(source.keys())
                    elif isinstance(source, (list, tuple, set, range)):
                        sequence = list(source)
                    elif isinstance(source, str):
                        sequence = list(source)
                    else:
                        try:
                            sequence = list(source)
                        except TypeError as error:
                            self.fail(token, f"for loop requires an iterable value: {error}", TypeError)
                    for item in sequence:
                        self.assign(target_var, item)
                        result = self.lines(body)
                        if isinstance(result, tuple):
                            if result[0] == "return":
                                return result[1]
                            if result[0] == "break":
                                break
                            if result[0] == "continue":
                                continue
                        last = result
                    return last
                if isinstance(callee, dict) and callee.get("type") == "identifier" and callee.get("value") == "on":
                    trigger_name = None
                    if target.get("children", [None, None])[1] is not None:
                        first_arg = target["children"][1]
                        if isinstance(first_arg, dict):
                            if first_arg.get("type") == "string":
                                trigger_name = first_arg.get("value")
                            elif first_arg.get("type") == "literal":
                                trigger_name = first_arg.get("value")
                            elif first_arg.get("type") == "identifier":
                                trigger_name = first_arg.get("value")
                    if trigger_name is None:
                        self.fail(token, "on() requires a trigger name", TypeError)
                    params: list[str] = []
                    defaults: dict[str, object] = {}
                    for child in target.get("children", [])[2:]:
                        if isinstance(child, dict) and child.get("type") == "assignment":
                            left, right = child.get("children", [None, None])
                            if isinstance(left, dict) and left.get("type") == "identifier" and right is not None:
                                params.append(left["value"])
                                defaults[left["value"]] = right
                            else:
                                self.fail(token, f"undefined name {child['value']!r}. if you meant to add a parameter, use {child['value']}=null instead.", SyntaxError)
                        elif isinstance(child, dict) and child.get("type") == "identifier":
                            params.append(child["value"])
                            defaults[child["value"]] = None
                    func = {"kind": "function", "name": None, "params": params, "defaults": defaults, "body": body, "scope": list(self.scopes), "trigger_name": str(trigger_name), "source": self.filename if self.filename not in {"<source>", ""} else "<source>", "content": self.content}
                    self._register_trigger_handler(func)
                    return func
                if isinstance(callee_value, dict) and callee_value.get("kind") == "function":
                    params: list[str] = []
                    defaults: dict[str, object] = {}
                    for child in target.get("children", [])[1:]:
                        if isinstance(child, dict) and child.get("type") == "assignment":
                            left, right = child.get("children", [None, None])
                            if isinstance(left, dict) and left.get("type") == "identifier" and right is not None:
                                params.append(left["value"])
                                defaults[left["value"]] = right
                                continue
                            self.fail(token, f"undefined name {child['value']!r}. if you meant to add a parameter, use {child['value']}=null instead.", SyntaxError)
                        if isinstance(child, dict) and child.get("type") == "identifier":
                            params.append(child["value"])
                            defaults[child["value"]] = None
                            continue
                        self.fail(token, f"undefined name {child['value']!r}. if you meant to add a parameter, use {child['value']}=null instead.", SyntaxError)
                    return {"kind": "function", "name": None, "params": params, "defaults": defaults, "body": body, "scope": list(self.scopes)}
                bases = []
                for child in target.get("children", [])[1:]:
                    value = self.value(child)
                    if isinstance(value, dict) and value.get("kind") == "class":
                        bases.append(value)
                    else:
                        bases.append(value)
                if isinstance(callee_value, dict) and callee_value.get("kind") == "class":
                    class_def = {"kind": "class", "name": None, "bases": [base for base in bases if isinstance(base, dict) and base.get("kind") == "class"], "methods": {}, "members": {}, "body": body, "scope": list(self.scopes)}
                    scope = {}
                    previous = self.scopes
                    self.scopes = list(self.scopes) + [scope]
                    try:
                        for line in body:
                            for statement in line:
                                result = self.value(statement)
                                if isinstance(statement, dict) and statement.get("type") == "assignment":
                                    left = statement["children"][0]
                                    if isinstance(left, dict) and left.get("type") == "identifier":
                                        name = left["value"]
                                        scope[name] = result
                                        class_def["members"][name] = result
                                        class_def[name] = result
                                        if isinstance(result, dict) and result.get("kind") in {"function", "class", "condition"}:
                                            result["owner"] = class_def
                                            class_def["methods"][name] = result
                        return class_def
                    finally:
                        self.scopes = previous

        value = self.value(target)

        if isinstance(value, dict) and value.get("kind") == "function":
            function_def = value
            if function_def.get("trigger_name") is not None:
                function_def["body"] = body
                function_def["scope"] = list(self.scopes)
                return function_def
            function_def = dict(value)
            function_def["body"] = body
            function_def["scope"] = list(self.scopes)
            return function_def

        if isinstance(value, dict) and value.get("kind") == "class":
            class_def = dict(value)
            class_def["body"] = body
            class_def["scope"] = list(self.scopes)
            class_def.setdefault("bases", [])
            class_def.setdefault("methods", {})
            class_def.setdefault("members", {})
            scope = {}
            previous = self.scopes
            self.scopes = list(self.scopes) + [scope]
            try:
                for line in body:
                    for statement in line:
                        result = self.value(statement)
                        if isinstance(statement, dict) and statement.get("type") == "assignment":
                            left = statement["children"][0]
                            if isinstance(left, dict) and left.get("type") == "identifier":
                                name = left["value"]
                                scope[name] = result
                                class_def["members"][name] = result
                                class_def[name] = result
                                if isinstance(result, dict) and result.get("kind") in {"function", "class", "condition"}:
                                    result["owner"] = class_def
                                    class_def["methods"][name] = result
                return class_def
            finally:
                self.scopes = previous

        if isinstance(value, dict) and value.get("kind") == "file":
            previous = self.scopes
            scope = {
                "file": value,
                "path": value.get("path"),
                "name": value.get("name"),
                "read": value.get("read"),
                "write": value.get("write"),
                "readline": value.get("readline"),
                "readlines": value.get("readlines"),
                "writelines": value.get("writelines"),
                "close": value.get("close"),
                "flush": value.get("flush"),
                "seek": value.get("seek"),
                "tell": value.get("tell"),
                "truncate": value.get("truncate"),
                "isatty": value.get("isatty"),
            }
            self.scopes = list(self.scopes) + [scope]
            try:
                return self.lines(body)
            finally:
                self.scopes = previous

        if isinstance(value, dict) and "__class__" in value:
            method = value.get("handler")
            if isinstance(method, dict):
                return self.invoke_function(method, [value, self.lines(body)], token)
            if callable(method):
                try:
                    return method(value, self.lines(body))
                except TypeError as error:
                    self.fail(token, str(error), type(error))

        if isinstance(value, dict) and value.get("kind") == "condition":
            return self.lines(body) if bool(value.get("value", False)) else None

        if isinstance(value, bool):
            return self.lines(body) if value else None

        if callable(value):
            try:
                return value(self.lines(body))
            except TypeError as error:
                self.fail(token, str(error), type(error))

        return value

    def import_file(self, path: str):
        path = (Path(self.filename).parent / path).with_suffix(".na")
        try:
            source = path.read_text()
        except OSError as error:
            raise ImportError(f"cannot import {path}: {error.strerror}")
        tokenizer = self.sodium.res.Tokenizer(self.sodium, self.options)
        previous = self.filename
        self.filename = str(path)
        try:
            return self.lines(tokenizer.run(source))
        finally:
            self.filename = previous
