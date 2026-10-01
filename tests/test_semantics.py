import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from sodium import SODIUM
from sodium.resources.Interpreter import Interpreter, SodiumError
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


def test_type_members_use_dot_and_object_members_use_colon():
    result = run_source('''
        print("hi":capitalize())
        print(string.capitalize("hi"))
    ''')
    assert result == 0


def test_dot_access_uses_class_static_members_and_colon_accesses_instance_members():
    result = run_source('''
        Person = class() {
            species = "human"
            init = function(self) {
                self.name = "danny"
            }
        }
        print(Person.species)
        print(Person().species)
        print(Person():name)
    ''')
    assert result == 0


def test_python_module_namespaces_allow_dot_access_for_static_members():
    result = run_source('''
        time = import_python("time")
        print(time.sleep)
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


def test_trigger_runs_registered_handlers_from_project_files(tmp_path, monkeypatch, capsys):
    module_path = tmp_path / "game.na"
    module_path.write_text('''
        on("playerSpawned", player = null) {
            print("Welcome, " + player.name)
        }
    ''')

    main = tmp_path / "main.na"
    main.write_text('''
        trigger("playerSpawned", { name: "Alice" })
        trigger("playerSpawned", { name: "Bob" })
    ''')

    monkeypatch.chdir(tmp_path)
    tokens = Tokenizer(SODIUM, []).run(main.read_text())
    Interpreter(SODIUM, []).run(tokens, main.read_text(), str(main))

    captured = capsys.readouterr()
    assert captured.out.count("Welcome") == 2
    assert "Alice" in captured.out
    assert "Bob" in captured.out


def test_trigger_discovery_raises_real_errors_for_bad_project_files(tmp_path, monkeypatch):
    (tmp_path / "broken.na").write_text('''
        on("start") {
            trigger("end"
        }
    ''')

    main = tmp_path / "main.na"
    main.write_text('''
        trigger("start")
    ''')

    monkeypatch.chdir(tmp_path)
    with pytest.raises(SyntaxError):
        Interpreter(SODIUM, []).run(
            Tokenizer(SODIUM, []).run(main.read_text()),
            main.read_text(),
            str(main),
        )


def test_instance_member_access_allows_trigger_handlers_in_module_files(tmp_path, monkeypatch, capsys):
    (tmp_path / "module.na").write_text('''
        on("start") {
            print("module start")
            trigger("end")
        }
    ''')
    (tmp_path / "module2.na").write_text('''
        on("end") {
            print("module2 end")
        }

        sqrt = import_python("math"):sqrt
    ''')

    main = tmp_path / "main.na"
    main.write_text('''
        trigger("start")
    ''')

    monkeypatch.chdir(tmp_path)
    Interpreter._trigger_registry.clear()
    Interpreter._trigger_scan_roots_done.clear()
    result = Interpreter(SODIUM, []).run(
        Tokenizer(SODIUM, []).run(main.read_text()),
        main.read_text(),
        str(main),
    )

    captured = capsys.readouterr()
    assert result == 0
    assert "module start" in captured.out
    assert "module2 end" in captured.out


def test_imported_modules_are_cached_and_only_execute_once(tmp_path, capsys):
    module_path = tmp_path / "counter.na"
    module_path.write_text('''
        print("loaded")
        value = 42
    ''')

    main = tmp_path / "main.na"
    main.write_text('''
        import "counter"
        import "counter"
        print(value)
    ''')

    result = Interpreter(SODIUM, []).run(Tokenizer(SODIUM, []).run(main.read_text()), main.read_text(), str(main))
    assert result == 0
    captured = capsys.readouterr()
    assert captured.out.count("loaded") == 1
    assert captured.out.count("42") == 1


def test_end_trigger_fires_when_main_program_finishes(tmp_path, monkeypatch, capsys):
    script = tmp_path / "main.na"
    script.write_text('''
        on("end") {
            print("goodbye")
        }
        print("hello")
    ''')

    monkeypatch.chdir(tmp_path)
    Interpreter._trigger_registry.clear()
    Interpreter._trigger_scan_roots_done.clear()
    result = Interpreter(SODIUM, []).run(
        Tokenizer(SODIUM, []).run(script.read_text()),
        script.read_text(),
        str(script),
    )
    captured = capsys.readouterr()
    assert result == 0
    assert "hello" in captured.out
    assert "goodbye" in captured.out


def test_end_trigger_only_fires_once_when_triggered_manually(tmp_path, monkeypatch, capsys):
    (tmp_path / "module.na").write_text('''
        on("start") {
            print("hi")
            trigger("end")
        }

        on("end") {
            print("end triggered")
        }
    ''')

    main = tmp_path / "main.na"
    main.write_text('''
        trigger("start")
    ''')

    monkeypatch.chdir(tmp_path)
    Interpreter._trigger_registry.clear()
    Interpreter._trigger_scan_roots_done.clear()
    result = Interpreter(SODIUM, []).run(
        Tokenizer(SODIUM, []).run(main.read_text()),
        main.read_text(),
        str(main),
    )
    captured = capsys.readouterr()
    assert result == 0
    assert captured.out.count("end triggered") == 1


def test_skelebash_boot_sequence_uses_startBonemarrow_and_relative_audio_path():
    repo_root = Path(__file__).resolve().parents[1]
    start_game = repo_root / "skelebash" / "skelebash" / "startGame.na"
    bonemarrow = repo_root / "skelebash" / "skelebash" / "bmrw" / "startBonemarrow.na"

    assert 'trigger("startBonemarrow")' in start_game.read_text()
    assert 'on("startBonemarrow")' in bonemarrow.read_text()
    assert 'play("{_here}/../sfx/start.wav")' in bonemarrow.read_text()


def test_placeholder_strings_evaluate_expressions_and_allow_escaped_braces(capsys):
    result = run_source('''
        print("value = {1 + 2}")
        print("raw \\{brace\\}")
    ''')
    captured = capsys.readouterr()
    assert result == 0
    assert "value = 3" in captured.out
    assert "raw {brace}" in captured.out


def test_from_syntax_binds_member_values_from_object():
    result = run_source('''
        obj = { value: 42 }
        value from obj
        print(value)
    ''')
    assert result == 0


def test_from_syntax_supports_module_member_binding():
    result = run_source('''
        json = import_python("json")
        dumps from json
        print(dumps is function)
    ''')
    assert result == 0


def test_this_and_here_resolve_to_current_file_and_parent_directory(tmp_path, monkeypatch, capsys):
    script = tmp_path / "game" / "main.na"
    script.parent.mkdir()
    script.write_text('''
        print(_this)
        print(_here)
    ''')

    monkeypatch.chdir(tmp_path)
    result = Interpreter(SODIUM, []).run(
        Tokenizer(SODIUM, []).run(script.read_text()),
        script.read_text(),
        str(script),
    )
    captured = capsys.readouterr()
    assert result == 0
    assert str(script.resolve()) in captured.out
    assert str(script.parent.resolve()) in captured.out


def test_this_and_here_are_correct_for_imported_files_and_triggers(tmp_path, monkeypatch, capsys):
    module = tmp_path / "module.na"
    module.write_text('''
        print(_this)
        print(_here)
        on("start") {
            print(_this)
            print(_here)
        }
    ''')

    main = tmp_path / "main.na"
    main.write_text('''
        import "module"
        trigger("start")
    ''')

    monkeypatch.chdir(tmp_path)
    result = Interpreter(SODIUM, []).run(
        Tokenizer(SODIUM, []).run(main.read_text()),
        main.read_text(),
        str(main),
    )
    captured = capsys.readouterr()
    assert result == 0
    assert str(module.resolve()) in captured.out
    assert str(module.parent.resolve()) in captured.out
    assert captured.out.count(str(module.resolve())) >= 2


def test_runtime_errors_capture_layered_frames():
    with pytest.raises(SodiumError) as excinfo:
        run_source('''
            outer = function() {
                inner = function() {
                    nope
                }
                inner()
            }
            outer()
        ''')

    error = excinfo.value
    assert error.kind == "name"
    names = [frame["name"] for frame in error.frames]
    assert "outer" in names
    assert "inner" in names
    assert "main" in names or any(name.startswith("<") for name in names)


def test_runtime_errors_report_the_call_site_for_callable_invocations():
    with pytest.raises(SodiumError) as excinfo:
        run_source('''
            play = import_python("math").sqrt
            play("bad")
        ''')

    error = excinfo.value
    assert any(frame.get("name") == "play" for frame in error.frames)


def test_trigger_handler_errors_keep_the_handler_frame(tmp_path, monkeypatch):
    module = tmp_path / "game.na"
    module.write_text('''
        on("start") {
            missing.name
        }
    ''')

    main = tmp_path / "main.na"
    main.write_text('''
        trigger("start")
    ''')

    monkeypatch.chdir(tmp_path)
    with pytest.raises(SodiumError) as excinfo:
        Interpreter(SODIUM, []).run(
            Tokenizer(SODIUM, []).run(main.read_text()),
            main.read_text(),
            str(main),
        )

    error = excinfo.value
    assert any("game.na" in frame.get("source", "") for frame in error.frames)
    assert any(frame.get("name") == "<anonymous>" or frame.get("name") == "main" for frame in error.frames)
    assert any(frame.get("name") == "trigger" for frame in error.frames)


def test_trigger_builtin_python_errors_keep_the_trigger_stack(tmp_path, monkeypatch):
    (tmp_path / "module.na").write_text('''
        on("start") {
            require from import_python("nitrogen")
            play from require("c")
            play("missing.wav")
        }
    ''')
    (tmp_path / "main.na").write_text('''
        trigger("start")
    ''')

    monkeypatch.chdir(tmp_path)
    with pytest.raises(SodiumError) as excinfo:
        Interpreter(SODIUM, []).run(
            Tokenizer(SODIUM, []).run((tmp_path / "main.na").read_text()),
            (tmp_path / "main.na").read_text(),
            str(tmp_path / "main.na"),
        )

    error = excinfo.value
    assert error.kind == "runtime"
    assert any(frame.get("name") == "trigger" for frame in error.frames)
    assert any("module.na" in frame.get("source", "") for frame in error.frames)
    assert "missing.wav" in str(error)


def test_cli_reports_trigger_handler_frame_in_traceback(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    (tmp_path / "module.na").write_text('''
        on("start") {
            test
        }
    ''')
    (tmp_path / "main.na").write_text('''
        trigger("start")
    ''')

    result = subprocess.run(
        [sys.executable, "-m", "sodium", "main.na"],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(repo_root)},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "Traceback (most recent call last):" in result.stdout
    assert "module.na" in result.stdout
    assert "on(\"start\")" in result.stdout
    assert "    test" in result.stdout
    assert "undefined name 'test'" in result.stdout


def test_error_output_clamps_highlight_to_line_end(capsys):
    SODIUM.script.error(TypeError("boom"), "main.na", 'trigger("startGame")', 250, 251, "runtime")
    output = re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)

    assert "main.na:1:250" not in output
    assert "main.na:1:20" in output
    assert "trigger(\"startGame\")" in output
    assert "runtime error" in output
    assert "boom" in output
    assert re.search(r"\s\^", output) is not None


def test_error_output_skips_caret_for_trigger_event_frames(capsys):
    SODIUM.script.error(
        TypeError("boom"),
        "main.na",
        "",
        0,
        1,
        "runtime",
        frames=[{
            "name": "<anonymous>",
            "source": "module.na",
            "position": 20,
            "end": 25,
            "content": 'on("end") {\n    require from import_python("nitrogen")\n    play("missing.wav")\n}',
            "trigger_name": "end",
        }],
    )
    output = re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)

    assert 'module.na:2 in <anonymous>' in output
    assert 'require from import_python("nitrogen")' in output
    assert re.search(r"\n\s+\|\s+\^+", output) is None


def test_error_output_keeps_source_and_message_when_no_snippet_is_available(capsys):
    SODIUM.script.error(
        TypeError("boom"),
        "main.na",
        "",
        9,
        10,
        "runtime",
        frames=[{"name": "main", "source": "main.na", "position": 9, "end": 10}],
    )
    output = re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)

    assert "main.na" in output
    assert "runtime error" in output
    assert "boom" in output
    assert re.search(r"main\.na:\d+:\d+", output) is not None


def test_error_output_keeps_full_call_stack_even_when_an_earlier_frame_has_no_content(capsys):
    SODIUM.script.error(
        TypeError("boom"),
        "main.na",
        'trigger("startGame")',
        0,
        1,
        "runtime",
        frames=[
            {"name": "outer", "source": "outer.na", "position": 12, "end": 13, "content": "outer()"},
            {"name": "inner", "source": "inner.na", "position": 4, "end": 5},
        ],
    )
    output = re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)

    assert "Traceback (most recent call last):" in output
    assert 'File "outer.na"' in output
    assert 'File "inner.na"' in output
    assert "runtime error" in output
    assert "boom" in output


def test_python_modules_and_objects_support_dot_member_access():
    result = run_source('''
        json = import_python("json")
        text = json.dumps({"ok": true})
        print(text)
    ''')
    assert result == 0


def test_top_level_run_returns_zero_for_success():
    assert run_source('x = 10') == 0
    assert run_source('print(1)') == 0
