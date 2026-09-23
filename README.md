<div align="center">

[![Sodium](sodium.png)](https://github.com/Wednesware/Sodium)

</div>

[![Wednesware](wednesware.png)](https://wednesware.org)

# Sodium

Sodium is a small, expressive scripting language designed to feel familiar while keeping a clear runtime model: values are first-class, functions and classes behave like variables, and execution is simple to read and debug.

It is intentionally lightweight. There are no hidden Python-only internals exposed to programs, and user-facing output is formatted in a language-native way instead of dumping raw runtime dictionaries.

## Dependencies

- Python 3.12+
- Nitrogen 26.62+ (`pip install wwn`)

## Running a program

Use the CLI entry point:

```bash
python -m sodium main.na
```

A file is executed top-to-bottom. If the program exits successfully, the interpreter returns status code 0. Even when the last statement evaluates to a value, the interpreter does not leak that raw value to the shell as a Python dict.

## Core syntax

### Statements and semicolons

Sodium accepts both newline-based and semicolon-based statement separation:

```sodium
x = 1
y = x + 2

print(y)
```

```sodium
x = 1; y = x + 2; print(y)
```

Semicolons work the same in blocks and handlers, so this is valid:

```sodium
if true {
    print("hello"); print("world")
}
```

### Values

Sodium supports:

- numbers: `42`, `3.14`
- strings: `"hello"`, `'hi'`
- booleans: `true`, `false`
- null: `null`
- arrays: `[1, 2, 3]`
- maps: `{ "name": "Sodium", "year": 2026 }`
- sets: `{1, 2, 3}`
- custom objects: `<name: value, ...>`

### Variables and assignment

```sodium
message = "hello"
count = 10
```

Variables are plain names and are resolved dynamically in lexical runtime scope.

## Functions

Functions are values just like any other variable. They are not special parser-only constructs.

```sodium
say_hello = function() {
    print("hello")
}

say_hello()
```

A function can be assigned, passed around, and printed as a runtime value:

```sodium
print(say_hello)
```

Result:

```text
<function say_hello>
```

Functions may take parameters:

```sodium
add = function(a, b) {
    return a + b
}

print(add(2, 3))
```

## Classes

Classes are also runtime values and can be bound to names. Instance objects are displayed in a clean language-native format.

```sodium
Thing = class() {
    init = function(self, value) {
        self.value = value
    }
}

thing = Thing("hi")
print(thing)
```

This prints a friendly name such as:

```text
<instance Thing>
```

## Conditions and handlers

Sodium conditionals are value-like and can be stored in variables:

```sodium
if(true) {
    print("runs")
}
```

A handler attaches a block to a value, function, class, or condition.

```sodium
watch = function {
    print("running")
}

watch {
    print("override block")
}
```

The runtime recognizes boolean conditions and truthy values as block triggers.

## Builtins

The standard runtime exposes a small set of builtins.

### Output and conversion

```sodium
print(value, value2)
string(value)
number(value)
boolean(value)
```

### Collection constructors

```sodium
array(1, 2, 3)
map("a": 1, "b": 2)
set(1, 2, 3)
bytes(value)
```

### Utility functions

```sodium
len(value)
input(prompt)
range(start, stop, step)
```

### Constants

```sodium
true
false
null
```

## Module imports

Sodium supports importing another `.na` file by module name.

```sodium
import "maths"
print(value)
print(add(2, 3))
```

If `maths.na` contains:

```sodium
value = 21 + 21

add = function(a, b) {
    return a + b
}
```

then the imported definitions become available in the current scope.

## Return statements

`return` works in functions and blocks:

```sodium
fact = function(n) {
    if n <= 1 {
        return 1
    }
    return n * fact(n - 1)
}
```

## Error handling and diagnostics

Sodium reports custom errors instead of raw Python tracebacks. They include:

- a kind (`syntax`, `runtime`, or `name`)
- the source file name
- the line and column number
- a highlighted offending span

Example:

```text
sodium: syntax error: unexpected ')'
  --> main.na:8:9
   8 | print(&)
     |         ^
```

The highlight is narrowed to the exact region involved rather than the whole line.

## Design principles

Sodium keeps a strict separation between:

- language values that users manipulate
- runtime metadata used internally
- user-visible formatting

This is why function and class objects are displayed as:

```text
<function name>
<class Name>
```

and not as raw Python dictionaries such as:

```python
{'__class__': {'kind': 'class', 'name': 'class', 'methods': {}}}
```

The latter is an internal representation leak and is intentionally avoided in the language runtime.

## Example program

```sodium
message = "hello from Sodium"

say_hello = function(name) {
    print("Hello, " + name)
    return "done"
}

print(message)
say_hello("friend")

Thing = class() {
    init = function(self, value) {
        self.value = value
    }
}

item = Thing("demo")
print(item)
```

## Notes

- Sodium is intentionally small and readable.
- Values are not forced into Python-only representations.
- Semicolons, blocks, and handlers are supported in the same style as the core syntax.
- The language runtime aims to be clear, debuggable, and human-friendly.

This documentation is intentionally written as a practical reference for the language as it exists today, focusing on syntax, runtime behavior, and usability.
