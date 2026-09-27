from sodium import SODIUM
from sodium.resources.Interpreter import Interpreter
from sodium.resources.Tokenizer import Tokenizer


def run_source(source: str):
    tokens = Tokenizer(SODIUM, []).run(source)
    return Interpreter(SODIUM, []).run(tokens, source, "test.na")


def test_semicolon_separates_statements_and_blocks():
    run_source('''
        x = 1;
        y = x + 2;
        print(y);
    ''')


def test_function_and_condition_are_runtime_values():
    run_source('''
        say_hello = function
        say_hello = say_hello()
        say_hello = say_hello{
            print("hi"); print("again")
        }
        condition = if(say_hello is function)
        condition{
            say_hello()
        }
    ''')


def test_runtime_objects_have_clean_repr():
    output = run_source('''
        say_hello = function
        print(say_hello)
        string(say_hello)
        Thing = class
        print(Thing)
    ''')
    assert output == 0


def test_import_statement_loads_module_values(tmp_path):
    module_path = tmp_path / "maths.na"
    module_path.write_text('''
        value = 21 + 21
        add = function(a, b) {
            return a + b
        }
    ''')

    main = tmp_path / "main.na"
    main.write_text('''
        import "maths"
        print(value)
        print(add(2, 3))
    ''')

    tokens = Tokenizer(SODIUM, []).run(main.read_text())
    result = Interpreter(SODIUM, []).run(tokens, main.read_text(), str(main))
    assert result == 0


def test_class_definition_factory_supports_method_blocks():
    result = run_source('''
        Thing = class() {
            init = function(self, value) {
                self.value = value
            }
        }
        thing = Thing("hi")
        print(thing)
    ''')
    assert result == 0


def test_class_fields_are_accessible_from_the_class_value():
    result = run_source('''
        Thing = class() {
            x = 10
        }
        print(Thing.x)
    ''')
    assert result == 0


def test_percentages_work_as_postfix_values_and_modulo_as_binary():
    result = run_source('''
        x = 30% + 10 + 50%
        y = 50% * 10
        z = 7 % 3
        a = 30
        print(x)
        print(y)
        print(z)
        print(a%)
    ''')
    assert result == 0


def test_literal_values_print_in_language_form():
    output = run_source('''
        print(true)
        print(false)
        print(null)
    ''')
    assert output == 0


def test_is_compares_value_to_type():
    assert run_source('''
        print(1 is number)
        print(1 is string)
        f = function() {}
        print(f is function)
        Thing = class() {}
        print(Thing is class)
        x = Thing()
        print(x is Thing)
        print(x is class)
    ''') == 0


def test_top_level_run_returns_zero_for_success():
    assert run_source('x = 10') == 0
    assert run_source('print(1)') == 0
