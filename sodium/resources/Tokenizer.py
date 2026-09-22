import re, json


class Tokenizer:
    def __init__(self, sodium, options: list[str]) -> None:
        self.sodium = sodium
        self.options = options
        self.content: str | None = None
        self.tokenized: list[list[dict]] = []
        self.tokens: list[dict] = []
        self.index = 0
        self.closers: list[str] = []
        self.error_position = 0
        self.error_end = 1

    def token(self, kind: str, value=None, children=None, position=None, end=None) -> dict:
        token = {"type": kind, "value": value, "children": children or []}
        if position is not None:
            token["position"] = position
            token["end"] = end if end is not None else position + 1
        return token

    def run(self, content: str) -> list[list[dict]]:
        self.content = content
        self.tokens = self.lex(content)
        self.index = 0
        self.tokenized = self.lines()
        self.validate(self.tokenized)
        if "show-tokens" in self.options:
            print(json.dumps(self.tokenized, indent=2, ensure_ascii=False))
        return self.tokenized

    def validate(self, values) -> None:
        for value in values:
            if isinstance(value, list):
                self.validate(value)
            elif value is not None:
                self.validate(value["children"])

    def lex(self, content: str) -> list[dict]:
        tokens, index = [], 0
        while index < len(content):
            char = content[index]
            if char in " \t\r":
                index += 1
            elif char == "#":
                index = content.find("\n", index)
                if index < 0:
                    break
            elif char == "\n":
                tokens.append(self.token("newline", "\n", position=index, end=index + 1))
                index += 1
            elif char in "'\"":
                start = index
                value, index = self.string(content, index)
                tokens.append(self.token("string", value, position=start, end=index))
            elif char == "b" and index + 1 < len(content) and content[index + 1] in "'\"":
                start = index
                value, index = self.string(content, index + 1)
                tokens.append(self.token("bytes", value.encode(), position=start, end=index))
            elif char.isdigit() or char == "." and index + 1 < len(content) and content[index + 1].isdigit():
                start = index
                match = re.match(r"(?:\d+\.?(?:\d*)?|\.\d+)(?:[eE][+-]?\d+)?", content[index:])
                value = match.group()
                index += len(value)
                if index < len(content) and content[index] == "%":
                    index += 1
                    tokens.append(self.token("number", float(value) / 100, position=start, end=index))
                else:
                    tokens.append(self.token("number", float(value) if any(c in value for c in ".eE") else int(value), position=start, end=index))
            elif char.isalpha() or char == "_":
                start = index
                match = re.match(r"[A-Za-z_][A-Za-z0-9_]*", content[index:])
                value = match.group()
                index += len(value)
                tokens.append(self.token("literal", {"true": True, "false": False, "null": None}[value], position=start, end=index) if value in {"true", "false", "null"} else self.token("identifier", value, position=start, end=index))
            else:
                start = index
                value = next((operator for operator in ("==", "!=", ">=", "<=", "**", "//") if content.startswith(operator, index)), char)
                index += len(value)
                tokens.append(self.token("symbol", value, position=start, end=index))
        tokens.append(self.token("eof", position=len(content), end=len(content)))
        return tokens

    def string(self, content: str, index: int) -> tuple[str, int]:
        start, quote, index, value = index, content[index], index + 1, ""
        while index < len(content) and content[index] != quote:
            if content[index] == "\\" and index + 1 < len(content):
                index += 1
                value += {"n": "\n", "t": "\t", "r": "\r"}.get(content[index], content[index])
            else:
                value += content[index]
            index += 1
        if index == len(content):
            self.error_position, self.error_end = start, index
            raise SyntaxError("unterminated string")
        return value, index + 1

    def peek(self, value=None) -> dict:
        token = self.tokens[self.index]
        self.error_position = token.get("position", self.error_position)
        self.error_end = token.get("end", self.error_position + 1)
        return token if value is None else token["value"] == value

    def take(self, value=None) -> dict:
        token = self.peek()
        if value is not None and token["value"] != value:
            raise SyntaxError(f"expected {value!r}, got {token['value']!r}")
        self.index += 1
        return token

    def lines(self, stop=None) -> list[list[dict]]:
        lines = []
        while self.peek()["type"] != "eof" and (stop is None or not self.peek(stop)):
            if self.peek("\n"):
                self.take()
            else:
                line = [self.statement()]
                while not self.peek("\n") and (stop is None or not self.peek(stop)) and self.peek()["type"] != "eof":
                    line.append(self.statement())
                lines.append(line)
                if self.peek("\n"):
                    self.take()
        return lines

    def statement(self) -> dict:
        if self.peek()["type"] == "identifier" and self.peek()["value"] == "return":
            self.take()
            return self.token("return", children=[] if self.peek("\n") or self.peek("}") else [self.expression()])
        return self.expression()

    def expression(self, minimum=0) -> dict:
        left = self.prefix()
        precedence = {"=": 1, "if": 2, "unless": 2, "or": 3, "and": 4,
                      "in": 5, "is": 5, "not in": 5, "is not": 5, "==": 5, "!=": 5,
                      ">": 5, "<": 5, ">=": 5, "<=": 5, "+": 6, "-": 6, "*": 7,
                      "/": 7, "//": 7, "%": 7, "^": 8, "**": 8}
        while True:
            if self.peek()["value"] in self.closers:
                break
            operator = self.operator()
            level = precedence.get(operator, -1)
            if level < minimum:
                break
            self.take()
            if operator in {"not in", "is not"}:
                self.take()
            left = self.token("assignment" if operator == "=" else "operator", operator, [left, self.expression(level + (operator != "=" and operator not in {"^", "**"}))])
        return left

    def operator(self) -> str:
        if self.peek()["type"] == "identifier":
            word = self.peek()["value"]
            if word == "not" and self.tokens[self.index + 1]["value"] in {"in", "is"}:
                return f"not {self.tokens[self.index + 1]['value']}"
            if word == "is" and self.tokens[self.index + 1]["value"] == "not":
                return "is not"
            return word
        return self.peek()["value"]

    def prefix(self) -> dict:
        if self.peek()["value"] in {"+", "-"} or self.peek()["type"] == "identifier" and self.peek()["value"] == "not":
            return self.token("unary", self.take()["value"], [self.prefix()])
        node = self.atom()
        while True:
            if self.peek("("):
                node = self.token("call", children=[node, *self.items("(", ")")])
            elif self.peek("["):
                self.take()
                start = None if self.peek(":") else self.expression()
                if self.peek(":"):
                    self.take()
                    end = None if self.peek("]") else self.expression()
                    self.take("]")
                    node = self.token("slice", children=[node, start, end])
                else:
                    self.take("]")
                    node = self.token("index", children=[node, start])
            elif self.peek("."):
                self.take()
                member = self.take()
                if member["type"] != "identifier":
                    raise SyntaxError("expected member name")
                node = self.token("member", member["value"], [node])
            elif self.peek("{"):
                self.take()
                node = self.token("handler", children=[node, *self.lines("}")])
                self.take("}")
            else:
                return node

    def atom(self) -> dict:
        token = self.take()
        if token["type"] in {"string", "number", "bytes", "literal", "identifier"}:
            return token
        if token["value"] == "(":
            node = self.expression()
            self.take(")")
            return node
        if token["value"] == "[":
            return self.token("array", children=self.items_open("]"))
        if token["value"] == "{":
            return self.collection("}")
        if token["value"] == "<":
            return self.token("custom", children=self.pairs(">"))
        raise SyntaxError(f"unexpected {token['value']!r}")

    def items(self, opening: str, closing: str) -> list[dict]:
        self.take(opening)
        return self.items_open(closing)

    def items_open(self, closing: str) -> list[dict]:
        values = []
        while not self.peek(closing):
            values.append(self.expression())
            if not self.peek(","):
                break
            self.take()
        self.take(closing)
        return values

    def collection(self, closing: str) -> dict:
        if self.peek(closing):
            self.take()
            return self.token("map")
        index = self.index
        first = self.expression()
        if self.peek(":"):
            self.index = index
            return self.token("map", children=self.pairs(closing))
        values = [first]
        while self.peek(","):
            self.take()
            values.append(self.expression())
        self.take(closing)
        return self.token("set", children=values)

    def pairs(self, closing: str) -> list[dict]:
        pairs = []
        self.closers.append(closing)
        try:
            while not self.peek(closing):
                key = self.expression()
                self.take(":")
                pairs.append(self.token("pair", children=[key, self.expression()]))
                if not self.peek(","):
                    break
                self.take()
            self.take(closing)
        finally:
            self.closers.pop()
        return pairs

    def returned(self, value=None) -> dict:
        return self.token("return", children=[] if value is None else [value])
