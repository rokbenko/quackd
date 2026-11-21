"""How systemd reads the onboard unit, re-implemented from systemd's C source for the suite.

No systemd runs where the suite runs, so the tests of `roboglot.onboard.systemd` read the unit
it writes with this, and three tests in `tests/test_onboard_wizard.py` hold it to cases systemd's
own test suite asserts (test-load-fragment.c `test_config_parse_exec`, test-user-util.c). It is
faithful to systemd v250 and later (the Jetson images from JetPack 5 on, Raspberry Pi OS, Ubuntu
22.04 and 24.04) for exactly the directives `systemd.unit_text` writes, and to nothing else:

- the file: `config_parse` (src/shared/conf-parser.c): lines end at LF, CR or NUL; a line whose
  first non-blank character is `#` or `;` is a comment, skipped even inside a continuation; a line
  ending in an odd run of backslashes goes on to the next, the backslash turned into a space;
  `parse_line` strips WHITESPACE (" \\t\\n\\r") from both ends and refuses the whole file
  (`return log_syntax_invalid_utf8(...)`, -EINVAL) when the line is not UTF-8 clean by systemd's
  own `utf8_is_valid`, whose `unichar_is_valid` (src/basic/utf8.c) refuses the surrogates, the
  noncharacters U+FDD0..U+FDEF and every code point whose low 16 bits are FFFE or FFFF;
- specifiers: `specifier_printf` (src/shared/specifier.c): `%%` is `%`, `%x` is expanded (or
  refused when unknown), a lone `%` at the very end is kept;
- `extract_first_word` (src/basic/extract-word.c) with EXTRACT_UNQUOTE and, where systemd passes
  it, EXTRACT_CUNESCAPE (`cunescape_one`, src/basic/escape.c);
- the directives (src/core/load-fragment.c): `config_parse_user_group_compat` (`User=`:
  specifiers, then `valid_user_group_name(VALID_USER_ALLOW_NUMERIC|VALID_USER_RELAX)` from
  src/basic/user-util.c, else -ENOEXEC), `config_parse_working_directory` (`-` and `~`, then
  `path_simplify_and_warn(PATH_CHECK_ABSOLUTE|PATH_CHECK_FATAL)`), `config_parse_environ`
  (words with UNQUOTE|CUNESCAPE, specifiers, `env_assignment_is_valid`, a bad word ignored),
  `config_parse_unit_env_file` (the whole value, specifiers, `path_simplify_and_warn`, ignored
  when that fails), `config_parse_exec` (the first word with UNQUOTE|CUNESCAPE, the `-@:+!`
  prefixes, specifiers, then -ENOEXEC for an empty path, for `!string_is_safe(path)`, which
  refuses a control character, a double or single quote, a backslash and DEL, for a path ending
  in `/`, and for an absolute path that is not `path_is_valid`), `config_parse_namespace_path_strv`
  (`ReadWritePaths=`: words with UNQUOTE alone, `-` and `+`, specifiers, `path_simplify_and_warn`,
  a bad one ignored);
- `get_user_creds` (src/basic/user-util.c), how a service's `User=` becomes a uid: `root` and `0`
  are uid 0 without asking the database, `nobody` and `65534` are 65534, a name `parse_uid` takes
  (decimal, no sign, no leading zero, not 65535 or 4294967295) is that uid, and any other name is
  looked up by name;
- glob(3) as `safe_glob` calls it (flags 0): a backslash makes the next character itself, and an
  unescaped `*`, `?` or `[` makes the name a pattern.

Not modelled: `$` substitution at exec time (it touches argv[0] only for this unit's program, and
the argument words are fixed), and anything this unit does not write.
"""

from __future__ import annotations

from dataclasses import dataclass, field

WHITESPACE = " \t\n\r"
COMMENTS = "#;"
PATH_MAX = 4096
NAME_MAX = 255


class Refused(Exception):
    """systemd refuses the unit as a whole: the load state is error or bad-setting."""


class _Syntax(Exception):
    """`extract_first_word` returned -EINVAL."""


def unichar_is_valid(point: int) -> bool:
    if point >= 0x110000:
        return False
    if (point & 0xFFFFF800) == 0xD800:
        return False
    if 0xFDD0 <= point <= 0xFDEF:
        return False
    return (point & 0xFFFE) != 0xFFFE


def utf8_is_valid(text: str) -> bool:
    return all(char != "\0" and unichar_is_valid(ord(char)) for char in text)


def specifiers(text: str) -> str:
    """`specifier_printf`; an expanded specifier comes back as `<%x>`, which never equals what
    the wizard meant, and an unknown one as -EBADSLT, raised."""
    out: list[str] = []
    percent = False
    for char in text:
        if percent:
            percent = False
            if char == "%":
                out.append("%")
            elif char.isascii() and char.isalpha():
                out.append(f"<%{char}>")
            else:
                raise _Syntax(f"unknown specifier %{char}")
        elif char == "%":
            percent = True
        else:
            out.append(char)
    if percent:
        out.append("%")
    return "".join(out)


def _unhex(char: str) -> int:
    return int(char, 16) if char and char in "0123456789abcdefABCDEF" else -1


def cunescape_one(text: str, at: int) -> tuple[str, int]:
    """The character `\\` followed by `text[at:]` stands for, and how many characters it took."""
    char = text[at] if at < len(text) else ""
    simple = {
        "a": "\a",
        "b": "\b",
        "f": "\f",
        "n": "\n",
        "r": "\r",
        "t": "\t",
        "v": "\v",
        "\\": "\\",
        '"': '"',
        "'": "'",
        "s": " ",
    }
    if char in simple and char:
        return simple[char], 1
    if char == "x":
        a, b = _unhex(text[at + 1 : at + 2]), _unhex(text[at + 2 : at + 3])
        if a < 0 or b < 0 or (a == 0 and b == 0):
            raise _Syntax("bad \\x")
        return chr(a * 16 + b), 3
    if char in ("u", "U"):
        width = 4 if char == "u" else 8
        digits = text[at + 1 : at + 1 + width]
        if len(digits) != width or any(_unhex(d) < 0 for d in digits):
            raise _Syntax("bad \\u")
        point = int(digits, 16)
        if point == 0 or (char == "U" and not unichar_is_valid(point)):
            raise _Syntax("bad \\u")
        return chr(point), width + 1
    if char and char in "01234567":
        digits = text[at : at + 3]
        if len(digits) != 3 or any(d not in "01234567" for d in digits):
            raise _Syntax("bad octal")
        value = int(digits, 8)
        if value == 0 or value > 255:
            raise _Syntax("bad octal")
        return chr(value), 3
    raise _Syntax(f"bad escape \\{char}")


def extract_first_word_and_warn(text: str) -> tuple[str | None, str]:
    """`extract_first_word_and_warn` with UNQUOTE|CUNESCAPE, as `config_parse_exec` calls it:
    on -EINVAL it tries again with EXTRACT_UNESCAPE_RELAX, and only then gives up."""
    try:
        return extract_first_word(text, cunescape=True)
    except _Syntax:
        return extract_first_word(text, cunescape=True, relax=True)


def extract_first_word(
    text: str, *, cunescape: bool, relax: bool = False
) -> tuple[str | None, str]:
    """`extract_first_word(&p, &word, NULL, EXTRACT_UNQUOTE[|EXTRACT_CUNESCAPE])`: the word and
    the rest, the rest starting at the next word; None at the end of the input."""
    i, n = 0, len(text)
    while i < n and text[i] in WHITESPACE:
        i += 1
    if i >= n:
        return None, ""
    out: list[str] = []
    quote, backslash = "", False
    while True:
        char = text[i] if i < n else ""
        if backslash:
            if not char:
                if relax and not quote:  # EXTRACT_UNESCAPE_RELAX keeps a trailing backslash
                    out.append("\\")
                    return "".join(out), ""
                raise _Syntax("trailing backslash")
            if cunescape:
                try:
                    got, used = cunescape_one(text, i)
                except _Syntax:
                    if not relax:
                        raise
                    got, used = "\\" + char, 1  # an unknown escape kept verbatim
                out.append(got)
                i += used - 1
            else:
                out.append(char)
            backslash = False
        elif quote:
            if not char:
                raise _Syntax("unbalanced quote")
            if char == quote:
                quote = ""
            elif char == "\\":
                backslash = True
            else:
                out.append(char)
        else:
            if not char:
                return "".join(out), ""
            if char in "'\"":
                quote = char
            elif char == "\\":
                backslash = True
            elif char in WHITESPACE:
                j = i
                while j < n and text[j] in WHITESPACE:
                    j += 1
                return "".join(out), text[j:]
            else:
                out.append(char)
        i += 1


def path_simplify(path: str) -> str:
    """`path_simplify`: slashes run together, `.` parts and a trailing slash gone; `..` kept."""
    absolute = path.startswith("/")
    parts = [part for part in path.split("/") if part not in ("", ".")]
    simple = ("/" if absolute else "") + "/".join(parts)
    return simple or ("/" if absolute else ".")


def path_is_valid(path: str) -> bool:
    encoded = path.encode("utf-8", "surrogatepass")
    if not encoded or len(encoded) >= PATH_MAX:
        return False
    return all(len(part) <= NAME_MAX for part in encoded.split(b"/"))


def path_is_normalized(path: str) -> bool:
    parts = path.split("/")
    if ".." in parts or "." in parts[1:] or path == ".":
        return False
    return "//" not in path


def path_simplify_and_warn(path: str) -> str | None:
    """With PATH_CHECK_ABSOLUTE: the simplified path, or None where systemd logs and fails."""
    if not utf8_is_valid(path) or not path.startswith("/"):
        return None
    simple = path_simplify(path)
    if not path_is_valid(simple) or not path_is_normalized(simple):
        return None
    return simple


def string_is_safe(text: str) -> bool:
    return not any(0 < ord(c) < 0x20 or c in "\"'\\\x7f" for c in text)


def parse_uid(text: str) -> int | None:
    if not text or not text.isascii() or not text.isdigit():
        return None
    if len(text) > 1 and text[0] == "0":
        return None
    value = int(text)
    if value > 0xFFFFFFFF or value in (0xFFFF, 0xFFFFFFFF):
        return None
    return value


def valid_user_name_relaxed(name: str) -> bool:
    """`valid_user_group_name(name, VALID_USER_ALLOW_NUMERIC|VALID_USER_RELAX)`."""
    if not name:
        return False
    if parse_uid(name) is not None:
        return True
    if name.startswith(" ") or name.endswith(" "):
        return False
    if not utf8_is_valid(name):
        return False
    if any(ord(c) < 0x20 or c == "\x7f" for c in name):
        return False
    if ":" in name or "/" in name:
        return False
    if name.isascii() and name.isdigit():
        return False
    if name[0] == "-" and all(c in "0123456789" for c in name[1:]):
        return False
    return name not in (".", "..")


def uid_for(name: str, by_name: dict[str, int]) -> int | None:
    """`get_user_creds`: the uid a service with `User=name` runs as, None when it cannot start."""
    if name in ("root", "0"):
        return 0
    if name in ("nobody", "65534"):
        return 65534
    numeric = parse_uid(name)
    if numeric is not None:
        return numeric if numeric in by_name.values() else None
    return by_name.get(name)


def glob_literal(pattern: str) -> str | None:
    """The one name `pattern` matches as glob(3) reads it, or None when it is a pattern."""
    out: list[str] = []
    chars = iter(pattern)
    for char in chars:
        if char == "\\":
            out.append(next(chars, "\\"))
        elif char in "*?[":
            return None
        else:
            out.append(char)
    return "".join(out)


@dataclass
class Service:
    """What systemd took from the `[Service]` section."""

    user: str | None = None
    working_directory: str | None = None
    environment: dict[str, str] = field(default_factory=dict)
    environment_files: list[tuple[bool, str | None]] = field(default_factory=list)
    exec_path: str | None = None
    exec_argv: list[str] = field(default_factory=list)
    read_write_paths: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)
    #: the `-@:+!` prefixes systemd took off the program, which change how it runs it
    exec_flags: str = ""
    #: whether a lone `;` began a second command line
    exec_more: bool = False


def _lines(text: str) -> list[str]:
    lines: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(text):
        char = text[i]
        if char in "\n\r\0":
            lines.append("".join(current))
            current = []
            if char == "\r" and text[i + 1 : i + 2] == "\n":
                i += 1
        else:
            current.append(char)
        i += 1
    if current:
        lines.append("".join(current))
    return lines


def logical_lines(text: str) -> list[str]:
    """`config_parse`'s loop: comments dropped, continuations joined."""
    out: list[str] = []
    continuation: str | None = None
    for raw in _lines(text):
        stripped = raw.lstrip(WHITESPACE)
        if stripped and stripped[0] in COMMENTS:
            continue
        p = raw if continuation is None else continuation + raw
        escaped = False
        for char in p:
            escaped = False if escaped else char == "\\"
        if escaped:
            continuation = p[:-1] + " "
            continue
        out.append(p)
        continuation = None
    if continuation is not None:
        out.append(continuation)
    return out


def read_unit(text: str) -> Service:
    """The unit as systemd loads it, or `Refused` saying why it does not."""
    service = Service()
    section = ""
    for line in logical_lines(text):
        line = line.strip(WHITESPACE)
        if not line:
            continue
        if not utf8_is_valid(line):
            raise Refused(f"String is not UTF-8 clean: {line!r}")
        if line.startswith("["):
            section = line[1:-1] if line.endswith("]") else ""
            continue
        key, equals, value = line.partition("=")
        if not equals:
            service.ignored.append(line)
            continue
        key, value = key.strip(WHITESPACE), value.strip(WHITESPACE)
        if section != "Service":
            continue
        try:
            _directive(service, key, value)
        except _Syntax as e:
            raise Refused(f"{key}={value!r}: {e}") from None
    return service


def _directive(service: Service, key: str, value: str) -> None:
    if key == "User":
        name = specifiers(value)
        if not valid_user_name_relaxed(name):
            raise Refused(f"Invalid user/group name or numeric ID: {name!r}")
        service.user = name
    elif key == "WorkingDirectory":
        missing_ok = value.startswith("-")
        rest = value[1:] if missing_ok else value
        if rest == "~":
            service.working_directory = "~"
            return
        simple = path_simplify_and_warn(specifiers(rest))
        if simple is None:
            if missing_ok:
                service.ignored.append(key)
                return
            raise Refused(f"WorkingDirectory= path is not valid: {rest!r}")
        service.working_directory = simple
    elif key == "Environment":
        rest = value
        while True:
            try:
                word, rest = extract_first_word(rest, cunescape=True)
            except _Syntax:
                service.ignored.append(f"{key}: invalid syntax")
                return
            if word is None:
                return
            try:
                resolved = specifiers(word)
            except _Syntax:
                service.ignored.append(f"{key}: {word!r}")
                continue
            name, equals, assigned = resolved.partition("=")
            if not equals or not name or not utf8_is_valid(assigned):
                service.ignored.append(f"{key}: {resolved!r}")
                continue
            service.environment[name] = assigned
    elif key == "EnvironmentFile":
        try:
            resolved = specifiers(value)
        except _Syntax:
            service.ignored.append(key)
            return
        optional = resolved.startswith("-")
        path = resolved[1:] if optional else resolved
        simple = path_simplify_and_warn(path)
        if simple is None:
            service.ignored.append(f"{key}: {path!r}")
            return
        service.environment_files.append((optional, glob_literal(simple)))
    elif key == "ExecStart":
        _exec(service, value)
    elif key == "ReadWritePaths":
        rest = value
        while True:
            try:
                word, rest = extract_first_word(rest, cunescape=False)
            except _Syntax:
                service.ignored.append(f"{key}: invalid syntax")
                return
            if word is None:
                return
            body = word.removeprefix("-").removeprefix("+")
            try:
                simple = path_simplify_and_warn(specifiers(body))
            except _Syntax:
                simple = None
            if simple is None:
                service.ignored.append(f"{key}: {word!r}")
                continue
            service.read_write_paths.append(simple)


def _exec(service: Service, value: str) -> None:
    first, rest = extract_first_word_and_warn(value)
    if first is None:
        return
    flags = ""
    while first and first[0] in "-@:+!" and first[0] not in flags:
        flags += first[0]
        first = first[1:]
    path = specifiers(first)
    if not path:
        raise Refused("Empty path in command line")
    if not string_is_safe(path):
        raise Refused(f"Executable name contains special characters: {path!r}")
    if path.endswith("/"):
        raise Refused(f"Executable path specifies a directory: {path!r}")
    if path.startswith("/") and not path_is_valid(path):
        raise Refused(f"Neither a valid executable name nor an absolute path: {path!r}")
    argv = [path] if "@" not in flags else []
    while rest:
        if rest[0] == ";" and (len(rest) == 1 or rest[1] in WHITESPACE):
            service.exec_more = True
            break
        word, rest = extract_first_word_and_warn(rest)
        if word is None:
            break
        argv.append(specifiers(word))
    service.exec_flags = flags
    service.exec_path = path_simplify(path)
    service.exec_argv = argv
