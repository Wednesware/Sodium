from pathlib import Path


class Interpreter:
    def __init__(self, sodium, options: list[str]) -> None:
        self.sodium = sodium
        self.options = options
        self.content: str | None = None
        self.error_position = 0
        self.error_end = 1
        self.scopes: list[dict] = []
        self.filename = "<source>"
        self.condition: bool | None = None
        self.matches: list[dict] = []

    def run(self, tokens: list[list[dict]], content: str = "", filename: str = "<source>"):
        self.content, self.filename = content, filename
        self.scopes = [self.builtins()]
        value = self.lines(tokens)
        if isinstance(value, tuple) and value[0] == "return":
            return value[1]
        return 0 if value is None else value

    def builtins(self) -> dict:
        return {
            "print": print, "input": input, "len": len, "range": range, "type": type,
            "string": str, "number": float, "boolean": bool, "array": list, "map": dict,
            "set": set, "bytes": bytes, "null": None, "import": self.import_file,
        }

    def fail(self, token: dict | None, message: str, kind=RuntimeError):
        token = token or {}
        self.mark(token)
        raise kind(message)

    def mark(self, token: dict):
        children = [child for child in token.get("children", []) if isinstance(child, dict)]
        self.error_position = token.get("position", children[0].get("position", self.error_position) if children else self.error_position)
        self.error_end = token.get("end", children[-1].get("end", self.error_position + 1) if children else self.error_position + 1)

    def lines(self, lines: list[list[dict]]):
        value = None
        previous, self.condition = self.condition, None
        try:
            for line in lines:
                for token in line:
                    value = self.value(token)
                    if isinstance(value, tuple) and value[0] == "return":
                        return value
            return value
        finally:
            self.condition = previous

    def value(self, token: dict | None):
        if token is None:
            return None
        self.mark(token)
        kind, value, children = token["type"], token["value"], token["children"]
        match kind:
            case "number" | "string" | "bytes" | "literal":
                return value
            case "identifier":
                return self.name(token)
            case "array":
                return [self.value(child) for child in children]
            case "set":
                return {self.value(child) for child in children}
            case "map" | "custom":
                return {self.value(pair["children"][0]): self.value(pair["children"][1]) for pair in children}
            case "member":
                return self.member(self.value(children[0]), value, token)
            case "index":
                return self.index(token)
            case "slice":
                return self.index(token, True)
            case "unary":
                return {"+": lambda item: +item, "-": lambda item: -item, "not": lambda item: not item}[value](self.value(children[0]))
            case "operator":
                left = self.value(children[0])
                if value == "and" and not left:
                    return left
                if value == "or" and left:
                    return left
                return self.operator(value, left, self.value(children[1]), token)
            case "assignment":
                result = self.value(children[1])
                self.assign(children[0], result)
                return result
            case "call":
                return self.call(token)
            case "handler":
                return self.handler(token)
            case "return":
                return ("return", self.value(children[0]) if children else None)
            case _:
                self.fail(token, f"unsupported token {kind!r}", SyntaxError)

    def name(self, token: dict):
        for scope in reversed(self.scopes):
            if token["value"] in scope:
                return scope[token["value"]]
        self.fail(token, f"undefined name {token['value']!r}", NameError)

    def assign(self, target: dict, value):
        if target["type"] == "identifier":
            for scope in reversed(self.scopes):
                if target["value"] in scope:
                    scope[target["value"]] = value
                    return
            self.scopes[-1][target["value"]] = value
        elif target["type"] == "member":
            self.set_member(self.value(target["children"][0]), target["value"], value, target)
        elif target["type"] == "index":
            self.value(target["children"][0])[self.value(target["children"][1])] = value
        else:
            self.fail(target, "assignment target must be a name, member, or index", SyntaxError)

    def index(self, token: dict, slicing=False):
        children = token["children"]
        try:
            key = slice(self.value(children[1]), self.value(children[2])) if slicing else self.value(children[1])
            return self.value(children[0])[key]
        except (IndexError, KeyError, TypeError) as error:
            self.fail(token, str(error), type(error))

    def operator(self, operator: str, left, right, token: dict):
        try:
            match operator:
                case "+": return left + right
                case "-": return left - right
                case "*": return left * right
                case "/": return left / right
                case "//": return left // right
                case "%": return left % right
                case "^" | "**": return left ** right
                case "==": return left == right
                case "!=": return left != right
                case ">": return left > right
                case "<": return left < right
                case ">=": return left >= right
                case "<=": return left <= right
                case "and": return left and right
                case "or": return left or right
                case "in": return left in right
                case "not in": return left not in right
                case "is": return type(left) is type(right)
                case "is not": return type(left) is not type(right)
        except (ArithmeticError, TypeError, ValueError) as error:
            self.fail(token, str(error))
        self.fail(token, f"unknown operator {operator!r}", SyntaxError)

    def member(self, object, name: str, token: dict):
        if isinstance(object, dict) and name in object:
            return object[name]
        if name.startswith("_") or not hasattr(object, name):
            self.fail(token, f"{type(object).__name__} has no member {name!r}", AttributeError)
        return getattr(object, name)

    def set_member(self, object, name: str, value, token: dict):
        if isinstance(object, dict):
            object[name] = value
        elif not name.startswith("_"):
            setattr(object, name, value)
        else:
            self.fail(token, f"cannot set member {name!r}", AttributeError)

    def call(self, token: dict):
        target, *arguments = token["children"]
        target_value = self.value(target)
        arguments = [self.value(argument) for argument in arguments]
        if isinstance(target_value, dict) and target_value.get("kind") == "function":
            owner = self.value(target["children"][0]) if target["type"] == "member" else None
            return self.function(target_value, ([owner] if owner is not None else []) + arguments, token)
        if isinstance(target_value, dict) and target_value.get("kind") == "class":
            return self.instance(target_value, arguments, token)
        if not callable(target_value):
            self.fail(target, "value is not callable", TypeError)
        try:
            return target_value(*arguments)
        except (TypeError, ValueError, KeyError, IndexError, AttributeError) as error:
            self.fail(token, str(error), type(error))

    def handler(self, token: dict):
        target, *body = token["children"]
        if target["type"] == "call" and target["children"][0]["type"] == "identifier":
            name = target["children"][0]["value"]
            raw = target["children"][1:]
            match name:
                case "function":
                    return {"kind": "function", "params": [argument["value"] for argument in raw], "body": body, "scope": self.scopes[:]}
                case "method":
                    return self.define_method(raw, body, token)
                case "if":
                    self.condition = bool(self.value(raw[0]))
                    return self.lines(body) if self.condition else None
                case "unless":
                    self.condition = not self.value(raw[0])
                    return self.lines(body) if self.condition else None
                case "elseif":
                    if self.condition:
                        return None
                    self.condition = bool(self.value(raw[0]))
                    return self.lines(body) if self.condition else None
                case "else":
                    if self.condition is None:
                        self.fail(token, "else must follow if, unless, or elseif", SyntaxError)
                    if self.condition:
                        return None
                    self.condition = True
                    return self.lines(body)
                case "while" | "until":
                    return self.loop(raw[0], body, name == "until")
                case "for":
                    return self.each(raw, body, token)
                case "class":
                    return self.class_value(raw, body)
                case "match":
                    return self.match(self.value(raw[0]), body)
                case "case":
                    if not self.matches:
                        self.fail(token, "case must be used inside match", SyntaxError)
                    if self.matches[-1]["matched"]:
                        return None
                    matched = not raw or self.matches[-1]["value"] == self.value(raw[0])
                    self.matches[-1]["matched"] = matched
                    return self.lines(body) if matched else None
        handler = self.value(target)
        if not callable(handler):
            self.fail(target, "handler target is not callable", TypeError)
        return handler(self.lines(body))

    def function(self, function: dict, arguments: list, token: dict):
        if len(arguments) != len(function["params"]):
            self.fail(token, f"expected {len(function['params'])} arguments, got {len(arguments)}", TypeError)
        previous, self.scopes = self.scopes, [*function["scope"], dict(zip(function["params"], arguments))]
        try:
            value = self.lines(function["body"])
            return value[1] if isinstance(value, tuple) and value[0] == "return" else value
        finally:
            self.scopes = previous

    def loop(self, condition: dict, body: list, invert: bool):
        value = None
        while bool(self.value(condition)) != invert:
            value = self.lines(body)
            if isinstance(value, tuple):
                return value
        return value

    def each(self, raw: list[dict], body: list, token: dict):
        if len(raw) != 2 or raw[0]["type"] != "identifier":
            self.fail(token, "for expects a name and iterable", SyntaxError)
        value = None
        for item in self.value(raw[1]):
            self.scopes.append({raw[0]["value"]: item})
            try:
                value = self.lines(body)
            finally:
                self.scopes.pop()
            if isinstance(value, tuple):
                return value
        return value

    def class_value(self, raw: list[dict], body: list):
        name = self.value(raw[0]) if raw else "class"
        value = {"kind": "class", "name": name, "methods": {}}
        self.scopes.append({"__class__": value})
        try:
            self.lines(body)
        finally:
            self.scopes.pop()
        return value

    def define_method(self, raw: list[dict], body: list, token: dict):
        if "__class__" not in self.scopes[-1] or not raw:
            self.fail(token, "method can only be used inside class", SyntaxError)
        name = self.value(raw[0])
        if not isinstance(name, str):
            self.fail(raw[0], "method name must be a string", TypeError)
        self.scopes[-1]["__class__"]["methods"][name] = {
            "kind": "function", "params": [argument["value"] for argument in raw[1:]], "body": body, "scope": self.scopes[:-1],
        }

    def instance(self, class_value: dict, arguments: list, token: dict):
        value = {"__class__": class_value, **class_value["methods"]}
        initializer = value.get("init")
        if initializer:
            self.function(initializer, [value, *arguments], token)
        elif arguments:
            self.fail(token, "class has no initializer", TypeError)
        return value

    def match(self, value, body: list):
        self.matches.append({"value": value, "matched": False})
        try:
            return self.lines(body)
        finally:
            self.matches.pop()

    def import_file(self, path: str):
        path = Path(self.filename).parent / path
        if path.suffix != ".na":
            path = path.with_suffix(".na")
        try:
            tokens = self.sodium.res.Tokenizer(self.sodium, self.options).run(path.read_text())
        except OSError as error:
            raise ImportError(f"cannot import {path}: {error.strerror}")
        previous = self.filename
        try:
            self.filename = str(path)
            return self.lines(tokens)
        finally:
            self.filename = previous
