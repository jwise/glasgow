# Ref: https://www.asset-intertech.com/eresources/svf-serial-vector-format-specification-jtag-boundary-scan
# Accession: G00022
# Ref: http://www.jtagtest.com/pdf/svf_specification.pdf
# Accession: G00023

import re
from abc import ABCMeta, abstractmethod

from ..support.bits import *


__all__ = ["SVFParser", "SVFEventHandler"]


def _hex_to_bits(input_nibbles):
    return bits(int(input_nibbles, 16))


_commands = (
    "ENDDR", "ENDIR", "FREQUENCY", "HDR", "HIR", "PIO", "PIOMAP", "RUNTEST",
    "SDR", "SIR", "STATE", "TDR", "TIR", "TRST",
)
_parameters = (
    "ENDSTATE", "HZ", "MASK", "MAXIMUM", "SCK", "SEC", "SMASK", "TCK", "TDI", "TDO",
)
_trst_modes = (
    "ON", "OFF", "Z", "ABSENT"
)
_tap_states = (
    "RESET", "IDLE", "DRSELECT", "DRCAPTURE", "DRSHIFT", "DREXIT1", "DRPAUSE",
    "DREXIT2", "DRUPDATE", "IRSELECT", "IRCAPTURE", "IRSHIFT", "IREXIT1", "IRPAUSE",
    "IREXIT2", "IRUPDATE",
)
_tap_stable_states = (
    "RESET", "IDLE", "IRPAUSE", "DRPAUSE"
)


class SVFParsingError(Exception):
    pass


class SVFLexer:
    """
    A Serial Vector Format lexer.

    Comments (``! comment``, ``// comment``) are ignored.

    The following tokens are recognized:
        * Keyword (``HIR``, ``SIR``, ``TIO``, ..., ``;``), returned as Python ``str``;
        * Integer (``8``, ``16``, ...), returned as Python ``int``;
        * Real (``1E0``, ``1E+0``, ``1E-0``, ...), returned as Python ``float``;
        * Bit array (``(0)``, ``(1234)``, ``(F00F)``, ...), returned as Python ``bits``;
        * Literal (``(HLUDXZHHLL)``, ``(IN FOO)``, ...), returned as Python ``tuple(str,)``;
        * End of file, returned as Python ``None``.

    :type buffer: str
    :attr buffer:
        Input buffer.

    :type position: int
    :attr position:
        Offset into buffer from which the next token will be read.
    """

    _keywords = _commands + _parameters + _trst_modes + _tap_states + (";",)
    _scanner  = tuple((re.compile(src, re.A|re.I|re.M), act) for src, act in (
        (r"\s+",
         None),
        (r"(?:!|//)([^\n]*)(?:\n|\Z)",
         None),
        (r"({})(?=\s+|[;()]|\Z)".format("|".join(_keywords)),
         lambda m: m[1]),
        (r"(\d+)(?=[^0-9\.E])",
         lambda m: int(m[1])),
        (r"(\d+(?:\.\d+)?(?:E[+-]?\d+)?)",
         lambda m: float(m[1])),
        (r"\(\s*([0-9A-F\s]+)\s*\)",
         lambda m: _hex_to_bits(re.sub(r"\s+", "", m[1]))),
        (r"\(\s*(.+?)\s*\)",
         lambda m: (m[1],)),
        (r"\Z",
         lambda m: None),
    ))

    def __init__(self, buffer):
        self.buffer   = buffer
        self.position = 0

    def line_column(self, position=None):
        """
        Return a ``(line, column)`` tuple for the given or, if not specified, current position.

        Both the line and the column start at 1.
        """
        line = len(re.compile(r"\n").findall(self.buffer, endpos=self.position))
        if line > 1:
            column = self.position - self.buffer.rindex("\n", 0, self.position)
        else:
            column = self.position
        return line + 1, column + 1

    def _lex(self):
        while True:
            for token_re, action in self._scanner:
                match = token_re.match(self.buffer, self.position)
                # print(token_re, match)
                if match:
                    if action is None:
                        self.position = match.end()
                        break
                    else:
                        return action(match), match.end()
            else:
                raise SVFParsingError("unrecognized SVF data at line %d, column %d (%s...)"
                                    % (*self.line_column(),
                                       self.buffer[self.position:self.position + 16]))

    def peek(self):
        """Return the next token without advancing the position."""
        token, _ = self._lex()
        return token

    def next(self):
        """Return the next token and advance the position."""
        token, next_pos = self._lex()
        self.position = next_pos
        return token

    def __iter__(self):
        return self

    def __next__(self):
        token = self.next()
        if token is None:
            raise StopIteration
        return token


class SVFParser:
    """
    A Serial Vector Format streaming parser.

    This parser maintains and allows querying lexical state (e.g. "sticky" ``TDI`` is
    automatically tracked), and invokes the SVF event handler for all commands so that
    any necessary action may be taken.
    """
    def __init__(self, buffer, handler):
        self._lexer     = SVFLexer(buffer)
        self._handler   = handler
        self._position  = 0
        self._token     = None
        self._cmd_pos   = 0

        self._param_tdi   = \
            {"HIR": None, "HDR": None, "SIR": None, "SDR": None, "TIR": None, "TDR": None}
        self._param_mask  = \
            {"HIR": None, "HDR": None, "SIR": None, "SDR": None, "TIR": None, "TDR": None}
        self._param_smask = \
            {"HIR": None, "HDR": None, "SIR": None, "SDR": None, "TIR": None, "TDR": None}

        self._param_run_state = "IDLE"
        self._param_end_state = "IDLE"

    def _try(self, action, *args):
        try:
            old_position = self._lexer.position
            return action(*args)
        except SVFParsingError as e:
            self._lexer.position = old_position
            return None

    def _parse_token(self):
        self._position = self._lexer.position
        self._token    = self._lexer.next()
        # print("token %s @ %d" % (self._token, self._position))
        return self._token

    def _parse_error(self, error):
        raise SVFParsingError("%s at line %d, column %d"
                              % (error, *self._lexer.line_column(self._position)))

    def _parse_unexpected(self, expected, valid=()):
        if isinstance(self._token, str):
            actual = self._token
        elif isinstance(self._token, int):
            actual = "integer"
        elif isinstance(self._token, float):
            actual = "real"
        elif isinstance(self._token, bits):
            actual = "scan data"
        elif isinstance(self._token, tuple):
            actual = "(%s)" % (*self._token,)
        elif self._token is None:
            actual = "end of file"
        else:
            assert False
        if valid:
            self._parse_error("expected %s (one of %s), found %s"
                              % (expected, ", ".join(valid), actual))
        else:
            self._parse_error("expected %s, found %s"
                              % (expected, actual))

    def _parse_keyword(self, keyword):
        if self._parse_token() == keyword:
            return self._token
        else:
            self._parse_unexpected("semicolon" if keyword == ";" else keyword)

    def _parse_keywords(self, keywords):
        if self._parse_token() in keywords:
            return self._token
        else:
            self._parse_unexpected("one of {}".format(", ".join(keywords)))

    def _parse_value(self, kind):
        if isinstance(self._parse_token(), kind):
            return self._token
        else:
            if kind == int:
                expected = "integer"
            elif kind == float:
                expected = "real"
            elif kind == (int, float):
                expected = "number"
            elif kind == bits:
                expected = "scan data"
            elif kind == tuple:
                expected = "data"
            else:
                assert False
            self._parse_unexpected(expected)

    def _parse_trst_mode(self):
        if self._parse_token() in _trst_modes:
            return self._token
        else:
            self._parse_unexpected("TRST mode", _trst_modes)

    def _parse_tap_state(self):
        if self._parse_token() in _tap_states:
            return self._token
        else:
            self._parse_unexpected("TAP state", _tap_states)

    def _parse_tap_stable_state(self):
        if self._parse_token() in _tap_stable_states:
            return self._token
        else:
            self._parse_unexpected("stable TAP state", _tap_stable_states)

    def _parse_scan_data(self, length):
        value = self._parse_value(bits)
        if int(value[length:]) != 0:
            self._parse_error("scan data length %d exceeds command length %d"
                              % (len(value), length))

        if length > len(value):
            return value + bits(0, length - len(value))
        else:
            return value[:length]

    def parse_command(self):
        self._cmd_pos = self._lexer.position

        command = self._parse_token()
        if command is None:
            return False

        elif command == "FREQUENCY":
            cycles = self._try(self._parse_value, (int, float))
            if cycles is not None:
                self._parse_keyword("HZ")
            self._parse_keyword(";")

            result = self._handler.svf_frequency(frequency=cycles)

        elif command == "TRST":
            mode = self._parse_trst_mode()
            self._parse_keyword(";")

            result = self._handler.svf_trst(mode=mode)

        elif command == "STATE":
            states = []
            while True:
                state = self._try(self._parse_tap_state)
                if state is None: break
                states.append(state)

            self._parse_keyword(";")

            if not states:
                self._parse_error("at least one state required")
            if states[-1] not in _tap_stable_states:
                self._parse_error("last state must be a stable state")

            *path_states, stable_state = states
            result = self._handler.svf_state(state=stable_state, path=path_states)

        elif command in ("ENDIR", "ENDDR"):
            stable_state = self._parse_tap_stable_state()
            self._parse_keyword(";")

            if command == "ENDIR":
                result = self._handler.svf_endir(state=stable_state)
            if command == "ENDDR":
                result = self._handler.svf_enddr(state=stable_state)

        elif command in ("HIR", "SIR", "TIR", "HDR", "SDR", "TDR"):
            length = self._parse_value(int)

            if self._param_mask[command] is None or len(self._param_mask[command]) != length:
                self._param_mask[command] = bits(-1, length)
            if self._param_smask[command] is None or len(self._param_smask[command]) != length:
                self._param_smask[command] = bits(-1, length)

            param_tdi   = self._param_tdi[command]
            param_tdo   = None
            param_mask  = self._param_mask[command]
            param_smask = self._param_smask[command]
            parameters  = set()
            while True:
                parameter = self._try(self._parse_keywords, ("TDI", "TDO", "MASK", "SMASK"))
                if parameter is None: break

                value = self._parse_scan_data(length)
                if parameter in parameters:
                    self._parse_error("parameter %s specified twice" % parameter)
                parameters.add(parameter)

                if parameter == "TDI":
                    self._param_tdi[command] = value
                    param_tdi = value
                if parameter == "TDO":
                    param_tdo = value
                if parameter == "MASK":
                    self._param_mask[command] = value
                    param_mask = value
                if parameter == "SMASK":
                    self._param_smask[command] = value
                    param_smask = value

            self._parse_keyword(";")

            if param_tdi is None and length == 0:
                param_tdi = bits()
            elif param_tdi is None:
                self._parse_error("initial value for parameter TDI required")
            if len(param_tdi) != length:
                self._parse_error("parameter TDI needs to be specified again because "
                                  "the length changed")

            if param_tdo is None:
                # Make it a bit easier for downstream; set MASK (but not remembered MASK)
                # to "all don't care" if there's no TDO specified.
                param_mask = bits(0, len(param_mask))

            if command == "HIR":
                result = self._handler.svf_hir(tdi=param_tdi, smask=param_smask,
                                               tdo=param_tdo,  mask=param_mask)
            if command == "SIR":
                result = self._handler.svf_sir(tdi=param_tdi, smask=param_smask,
                                               tdo=param_tdo,  mask=param_mask)
            if command == "TIR":
                result = self._handler.svf_tir(tdi=param_tdi, smask=param_smask,
                                               tdo=param_tdo,  mask=param_mask)
            if command == "HDR":
                result = self._handler.svf_hdr(tdi=param_tdi, smask=param_smask,
                                               tdo=param_tdo,  mask=param_mask)
            if command == "SDR":
                result = self._handler.svf_sdr(tdi=param_tdi, smask=param_smask,
                                               tdo=param_tdo,  mask=param_mask)
            if command == "TDR":
                result = self._handler.svf_tdr(tdi=param_tdi, smask=param_smask,
                                               tdo=param_tdo,  mask=param_mask)

        elif command == "RUNTEST":
            run_state = self._try(self._parse_tap_stable_state)
            run_params = self._try(lambda:
                (self._parse_value(int), self._parse_keywords(("TCK", "SCK"))))
            if run_params is None:
                run_count, run_clock = None, "TCK"
                min_time, _ = \
                     self._parse_value((int, float)), self._parse_keyword("SEC")
            else:
                run_count, run_clock = run_params
                min_time, _ = self._try(lambda:
                    (self._parse_value((int, float)), self._parse_keyword("SEC"))) \
                    or (None, None)
            if self._try(self._parse_keyword, "MAXIMUM"):
                max_time, _ = \
                     self._parse_value((int, float)), self._parse_keyword("SEC")
            else:
                max_time = None
            if self._try(self._parse_keyword, "ENDSTATE"):
                end_state = self._parse_tap_stable_state()
            else:
                end_state = None
            self._parse_keyword(";")

            if run_state is None:
                run_state = self._param_run_state
            else:
                self._param_run_state = run_state
                if end_state is None:
                    end_state = run_state

            if end_state is None:
                end_state = self._param_end_state
            else:
                self._param_end_state = end_state

            if run_clock is None:
                run_clock = "TCK"

            if max_time is not None and min_time is not None and max_time < min_time:
                self._parse_error("maximum time must be greater than minimum time")

            result = self._handler.svf_runtest(run_state=run_state,
                                      run_count=run_count, run_clock=run_clock,
                                      min_time =min_time,   max_time=max_time,
                                      end_state=end_state)

        elif command == "PIOMAP":
            mapping, = self._parse_value(tuple)
            self._parse_keyword(";")

            result = self._handler.svf_piomap(mapping=mapping)

        elif command == "PIO":
            vector, = self._parse_value(tuple)
            self._parse_keyword(";")

            result = self._handler.svf_pio(vector=vector)

        else:
            self._parse_unexpected("command", _commands)

        return result or True

    def last_command(self):
        return self._lexer.buffer[self._cmd_pos:self._lexer.position]

    def parse_file(self):
        while self.parse_command(): pass


class SVFEventHandler(metaclass=ABCMeta):
    """
    An abstract base class for Serial Vector Format parsing events.

    The methods of this class are called when a well-formed SVF command is encountered.
    The parser takes care of maintaining all lexical state (e.g. "sticky" parameters),
    but all logical state is maintained by the event handler.
    """

    @abstractmethod
    def svf_frequency(self, frequency):
        """Called when the ``FREQUENCY`` command is encountered."""

    @abstractmethod
    def svf_trst(self, mode):
        """Called when the ``TRST`` command is encountered."""

    @abstractmethod
    def svf_state(self, state, path):
        """Called when the ``STATE`` command is encountered."""

    @abstractmethod
    def svf_endir(self, state):
        """Called when the ``ENDIR`` command is encountered."""

    @abstractmethod
    def svf_enddr(self, state):
        """Called when the ``ENDDR`` command is encountered."""

    @abstractmethod
    def svf_hir(self, tdi, smask, tdo, mask):
        """Called when the ``HIR`` command is encountered."""

    @abstractmethod
    def svf_sir(self, tdi, smask, tdo, mask):
        """Called when the ``SIR`` command is encountered."""

    @abstractmethod
    def svf_tir(self, tdi, smask, tdo, mask):
        """Called when the ``TIR`` command is encountered."""

    @abstractmethod
    def svf_hdr(self, tdi, smask, tdo, mask):
        """Called when the ``HDR`` command is encountered."""

    @abstractmethod
    def svf_sdr(self, tdi, smask, tdo, mask):
        """Called when the ``SDR`` command is encountered."""

    @abstractmethod
    def svf_tdr(self, tdi, smask, tdo, mask):
        """Called when the ``TDR`` command is encountered."""

    @abstractmethod
    def svf_runtest(self, run_state, run_count, run_clock, min_time, max_time, end_state):
        """Called when the ``RUNTEST`` command is encountered."""

    @abstractmethod
    def svf_piomap(self, mapping):
        """Called when the ``PIOMAP`` command is encountered."""

    @abstractmethod
    def svf_pio(self, vector):
        """Called when the ``PIO`` command is encountered."""
