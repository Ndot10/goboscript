from __future__ import annotations
import math
from typing import TYPE_CHECKING
from typing import Any
from typing import Iterator
from typing import cast
from difflib import get_close_matches
from itertools import chain
from lib import tok
from lib import number
from lib import num_plural
from sb3 import List
from sb3 import Block
from sb3 import Input
from sb3 import Stack
from sb3 import ProcDef
from sb3 import Argument
from sb3 import HatBlock
from sb3 import ProcCall
from sb3 import Variable
from gerror import FileError
from gerror import TokenError
from gparser import literal
from lark.lexer import Token
from sb3.gblock import ConditionBlock
from lark.visitors import Transformer
from sb3.gblockfactory import reporter_prototypes
from sb3.gblockfactory import statement_prototypes

if TYPE_CHECKING:
    from gdefinitionvisitor import Function
    from gdefinitionvisitor import DefinitionVisitor


def coerce_condition(input: Input, *, negate: bool = False) -> Block:
    if isinstance(input, ConditionBlock):
        if negate:
            return ConditionBlock("operator_not", {"OPERAND": input}, {})
        return input
    if isinstance(input, List):
        block = ConditionBlock(
            "operator_equals",
            {
                "OPERAND1": "0",
                "OPERAND2": Block("data_lengthoflist", {}, {"LIST": input}),
            },
            {},
        )
        if negate:
            return block
        return ConditionBlock("operator_not", {"OPERAND": block}, {})
    block = ConditionBlock(
        "operator_equals",
        {"OPERAND1": "0", "OPERAND2": input},
        {},
        comment="auto-generated",
    )
    if negate:
        return block
    return ConditionBlock("operator_not", {"OPERAND": block}, {})


def mkelif(
    rest: Iterator[tuple[Input, Stack]],
    this: tuple[Input, Stack] | None = None,
    _else: Stack | None = None,
) -> Block:
    if this is None:
        this = next(rest)
    try:
        nxt = next(rest)
    except StopIteration:
        if _else is None:
            return Block(
                "control_if",
                {
                    "CONDITION": coerce_condition(this[0]),
                    "SUBSTACK": coerce_condition(this[1]),
                },
                {},
            )
        return Block(
            "control_if_else",
            {
                "CONDITION": coerce_condition(this[0]),
                "SUBSTACK": coerce_condition(this[1]),
                "SUBSTACK2": _else,
            },
            {},
        )
    return Block(
        "control_if_else",
        {
            "CONDITION": coerce_condition(this[0]),
            "SUBSTACK": coerce_condition(this[1]),
            "SUBSTACK2": Stack([mkelif(rest, nxt, _else)]),
        },
        {},
    )


class BlockTransformer(Transformer[Token, Block]):
    def __init__(
        self,
        gdefinitionvisitor: DefinitionVisitor,
        prototype: Function | None = None,
    ):
        super().__init__(visit_tokens=True)
        self.gdefinitionvisitor = gdefinitionvisitor
        self.sprite = gdefinitionvisitor.sprite
        self.prototype = prototype

    def STRING(self, token: Token):  # noqa: N802
        return literal(token)

    NUMBER = STRING
    FLOAT = STRING
    CONST = STRING

    def start(self, args: tuple[Block]):
        return args[0]

    def expr(self, args: tuple[Input]):
        return args[0]

    def argument(self, args: tuple[Token]):
        if not self.prototype:
            msg = "Argument reporter used outsite function declaration"
            raise TokenError(msg, args[0])
        argument = literal(args[0])
        if argument not in self.prototype.arguments:
            matches = get_close_matches(argument, self.prototype.arguments)
            msg = "Undefined function argument"
            raise TokenError(
                msg, args[0], f"Did you mean `${matches[0]}`?" if matches else None
            )
        return Argument(argument)

    def declr_function(self, args: list[Any], *, warp: bool = True) -> ProcDef:
        name: Token = args[0]
        arguments: list[Token] = args[1:-1]
        if arguments == [None]:
            arguments = []
        stack: Stack = args[-1]
        return ProcDef(name, arguments, stack, warp=warp)

    def declr_function_nowarp(self, args: list[Any]) -> ProcDef:
        return self.declr_function(args, warp=False)

    def stack(self, args: list[Block]) -> Stack:
        stack = Stack.new(args)
        for i, block in enumerate(stack):
            if block.opcode == "control_forever" and (i + 1) != len(stack):
                msg = "forever cannot be precceded by any statements"
                raise FileError(msg)  # FIXME: switch to gTokenError but cannot because
        return stack

    def block(self, args: list[Any]) -> Block:
        opcode: Token = args[0]
        arguments: list[Input] = args[1:-1]
        comment: str | None = literal(args[-1]) if args[-1] else None
        if arguments == [None]:
            arguments = []
        if opcode in statement_prototypes:
            prototype = statement_prototypes[opcode]
            if len(arguments) > len(prototype.arguments):
                msg = "Too many arguments for statement"
                raise TokenError(
                    msg,
                    opcode,
                    f"Expected {num_plural(len(prototype.arguments), ' argument')}",
                )
            if len(arguments) < len(prototype.arguments):
                msg = "Missing arguments for statement"
                raise TokenError(
                    msg,
                    opcode,
                    f"Missing {', '.join(prototype.arguments[len(arguments):])}",
                )
            return Block.from_prototype(prototype, arguments, comment)
        if opcode in self.gdefinitionvisitor.functions:
            prototype = self.gdefinitionvisitor.functions[opcode]
            if len(arguments) > len(prototype.arguments):
                raise TokenError(
                    "Too many arguments for function",
                    opcode,
                    f"Expected {num_plural(len(prototype), ' argument')}",
                )
            if len(arguments) < len(prototype.arguments):
                raise TokenError(
                    "Missing arguments for function",
                    opcode,
                    f"Missing {', '.join(prototype.arguments[len(arguments):])}",
                )
            return ProcCall(
                opcode,
                dict(zip(prototype.arguments, arguments)),
                comment,
                prototype.proccode,
                warp=prototype.warp,
            )
        matches = get_close_matches(
            args[0],
            chain(
                statement_prototypes.keys(),
                self.gdefinitionvisitor.functions.keys(),
            ),
        )
        raise TokenError(
            f"Undefined statement or function `{opcode}`",
            args[0],
            (f"Did you mean `{matches[0]}`?\n" if matches else "")
            + "Read --doc statements for available statements",
        )

    def reporter(self, args: list[Any]) -> Input:
        opcode: Token = args[0]
        arguments: list[Input] = args[1:]
        if arguments == [None]:
            arguments = []
        if opcode not in reporter_prototypes:
            matches = get_close_matches(str(args[0]), reporter_prototypes.keys())
            raise TokenError(
                f"Undefined reporter `{opcode}`",
                args[0],
                (f"Did you mean `{matches[0]}`?\n" if matches else "")
                + "Read --doc reporters for available reporters",
            )
        prototype = reporter_prototypes[opcode]
        if len(arguments) > len(prototype.arguments):
            raise TokenError(
                "Too many arguments for reporter",
                opcode,
                f"Expected {num_plural(len(prototype.arguments), ' argument')}",
            )
        if len(arguments) < len(prototype.arguments):
            raise TokenError(
                "Missing arguments for reporter",
                opcode,
                f"Missing {', '.join(prototype.arguments[len(arguments):])}",
            )
        if (
            prototype.opcode == "operator_mathop.OPERATOR=sqrt"
            and type(arguments[0]) is str
        ):
            return str(math.sqrt(number(arguments[0])))
        return Block.from_prototype(prototype, arguments)

    def add(self, args: list[Input]):
        if type(args[0]) is str and type(args[1]) is str:
            try:
                return str(number(args[0]) + number(args[1]))
            except ValueError:
                pass
        return Block.from_prototype(reporter_prototypes["add"], args)

    def sub(self, args: list[Input]):
        if type(args[0]) is str and type(args[1]) is str:
            try:
                return str(number(args[0]) - number(args[1]))
            except ValueError:
                pass
        return Block.from_prototype(reporter_prototypes["sub"], args)

    def minus(self, args: tuple[Input]):
        if type(args[0]) is str:
            if args[0].startswith("-"):
                return args[0].removeprefix("-")
            return "-" + args[0]
        return Block.from_prototype(reporter_prototypes["sub"], ["0", args[0]])

    def mul(self, args: list[Input]):
        if type(args[0]) is str and type(args[1]) is str:
            return str(number(args[0]) * number(args[1]))
        return Block.from_prototype(reporter_prototypes["mul"], args)

    def div(self, args: list[Input]):
        if type(args[0]) is str and type(args[1]) is str:
            return str(number(args[0]) / number(args[1]))
        return Block.from_prototype(reporter_prototypes["div"], args)

    def mod(self, args: list[Input]):
        if type(args[0]) is str and type(args[1]) is str:
            return str(number(args[0]) % number(args[1]))
        return Block.from_prototype(reporter_prototypes["mod"], args)

    def join(self, args: list[Input]):
        if type(args[0]) is str and type(args[1]) is str:
            return args[0] + args[1]
        return Block.from_prototype(reporter_prototypes["join"], args)

    def eq(self, args: list[Input]):
        return ConditionBlock.from_prototype(reporter_prototypes["eq"], args)

    def neq(self, args: list[Input]):
        return ConditionBlock.from_prototype(
            reporter_prototypes["NOT"],
            [ConditionBlock.from_prototype(reporter_prototypes["eq"], args)],
        )

    def gt(self, args: list[Input]):
        return ConditionBlock.from_prototype(reporter_prototypes["gt"], args)

    def lt(self, args: list[Input]):
        return ConditionBlock.from_prototype(reporter_prototypes["lt"], args)

    def ge(self, args: list[Input]):
        return ConditionBlock.from_prototype(
            reporter_prototypes["NOT"],
            [ConditionBlock.from_prototype(reporter_prototypes["lt"], args)],
        )

    def le(self, args: list[Input]):
        return ConditionBlock.from_prototype(
            reporter_prototypes["NOT"],
            [ConditionBlock.from_prototype(reporter_prototypes["gt"], args)],
        )

    def andop(self, args: tuple[Input, Input]):
        left = coerce_condition(args[0])
        right = coerce_condition(args[1])
        if left.opcode == "operator_not" and right.opcode == "operator_not":
            return ConditionBlock.from_prototype(
                reporter_prototypes["NOT"],
                [
                    ConditionBlock.from_prototype(
                        reporter_prototypes["OR"],
                        [left.inputs["OPERAND"], right.inputs["OPERAND"]],
                    )
                ],
            )
        return ConditionBlock.from_prototype(reporter_prototypes["AND"], [left, right])

    def orop(self, args: tuple[Input, Input]):
        left = coerce_condition(args[0])
        right = coerce_condition(args[1])
        if left.opcode == "operator_not" and right.opcode == "operator_not":
            return ConditionBlock.from_prototype(
                reporter_prototypes["NOT"],
                [
                    ConditionBlock.from_prototype(
                        reporter_prototypes["AND"],
                        [left.inputs["OPERAND"], right.inputs["OPERAND"]],
                    )
                ],
            )
        return Block.from_prototype(reporter_prototypes["OR"], [left, right])

    def notop(self, args: tuple[Input]):
        operand = coerce_condition(args[0])
        if operand.opcode == "operator_not":
            return operand.inputs["OPERAND"]
        return ConditionBlock.from_prototype(reporter_prototypes["NOT"], [operand])

    def inop(self, args: tuple[Input, Input]):
        if isinstance(args[1], List):
            return ConditionBlock(
                "data_listcontainsitem", {"ITEM": args[0]}, {"LIST": args[1]}
            )
        return Block.from_prototype(reporter_prototypes["contains"], [args[1], args[0]])

    def block_if(self, args: tuple[Input, Stack]):
        return Block(
            "control_if",
            {"CONDITION": coerce_condition(args[0]), "SUBSTACK": args[1]},
            {},
        )

    def block_if_else(self, args: tuple[Input, Stack, Stack]):
        return Block(
            "control_if_else",
            {
                "CONDITION": coerce_condition(args[0]),
                "SUBSTACK": args[1],
                "SUBSTACK2": args[2],
            },
            {},
        )

    def block_if_elif(self, args: list[Any]):
        return mkelif(cast(Any, zip(*[iter(args)] * 2)))

    def block_if_elif_else(self, args: list[Any]):
        return mkelif(cast(Any, zip(*[iter(args[:-1])] * 2)), _else=args[-1])

    def until(self, args: tuple[Input, Stack]):
        return Block(
            "control_repeat_until",
            {"CONDITION": coerce_condition(args[0]), "SUBSTACK": args[1]},
            {},
        )

    def repeat(self, args: tuple[Input, Stack]):
        return Block("control_repeat", {"TIMES": args[0], "SUBSTACK": args[1]}, {})

    def forever(self, args: tuple[Stack]):
        return Block("control_forever", {"SUBSTACK": args[0]}, {})

    def localvar(self, args: tuple[Token, Input]):
        variable = self.get_variable(args[0])
        if not self.prototype:
            msg = "local variables cannot be used outside of functions"
            raise TokenError(msg, args[0], help="switch to a non-local variable")
        return Block("data_setvariableto", {"VALUE": args[1]}, {"VARIABLE": variable})

    def var(self, args: tuple[Token]):
        return self.get_identifier(args[0])

    def varset(self, args: tuple[Token, Input]):
        variable = self.get_variable(args[0])
        return Block("data_setvariableto", {"VALUE": args[1]}, {"VARIABLE": variable})

    def var_binop(self, opcode: str, args: tuple[Token, Input]):
        variable = self.get_variable(args[0])
        return Block(
            "data_setvariableto",
            {
                "VALUE": Block.from_prototype(
                    reporter_prototypes[opcode], [variable, args[1]]
                )
            },
            {"VARIABLE": variable},
        )

    def varmul(self, args: Any):
        return self.var_binop("mul", args)

    def vardiv(self, args: Any):
        return self.var_binop("div", args)

    def varmod(self, args: Any):
        return self.var_binop("mod", args)

    def varjoin(self, args: Any):
        return self.var_binop("join", args)

    def varchange(self, args: tuple[Token, Input]):
        variable = self.get_variable(args[0])
        return Block(
            "data_changevariableby", {"VALUE": args[1]}, {"VARIABLE": variable}
        )

    def varinc(self, args: tuple[Token]):
        variable = self.get_variable(args[0])
        return Block("data_changevariableby", {"VALUE": "1"}, {"VARIABLE": variable})

    def varsub(self, args: tuple[Token, Input]):
        variable = self.get_variable(args[0])
        return Block(
            "data_changevariableby",
            {"VALUE": Block.from_prototype(reporter_prototypes["sub"], ["0", args[1]])},
            {"VARIABLE": variable},
        )

    def get_identifier(self, token: Token):
        name = str(token)

        if self.prototype and name in self.prototype.locals:
            return self.sprite.variables[f"{self.prototype.name}:{name}"]

        if name in self.sprite.variables:
            return self.sprite.variables[name]

        if name in self.sprite.lists:
            return self.sprite.lists[name]

        if name in self.gdefinitionvisitor.globals:
            return Variable(name, tok(name))

        if name in self.gdefinitionvisitor.listglobals:
            return List(tok(name), None)

        help = None
        if matches := get_close_matches(name, self.sprite.variables.keys()):
            help = f"Did you mean the variable `{matches[0]}?`"
        elif matches := get_close_matches(name, self.sprite.lists.keys()):
            help = f"Did you mean the list `{matches[0]}?`"
        elif self.prototype and (
            matches := get_close_matches(name, self.prototype.locals)
        ):
            help = f"Did you mean the local variable `{matches[0]}?`"

        msg = f"Undefined variable or list `{token}`"
        raise TokenError(msg, token, help)

    def listset(self, args: list[Any]):
        list_ = self.get_list(args[0])
        block = Block("data_deletealloflist", {}, {"LIST": list_})
        if args[1] is None:
            return block
        return [
            block,
            *(Block("data_addtolist", {"ITEM": i}, {"LIST": list_}) for i in args[1:]),
        ]

    def listadd(self, args: tuple[Token, Input]):
        list_ = self.get_list(args[0])
        return Block("data_addtolist", {"ITEM": args[1]}, {"LIST": list_})

    def listdelete(self, args: tuple[Token, Input]):
        list_ = self.get_list(args[0])
        return Block("data_deleteoflist", {"INDEX": args[1]}, {"LIST": list_})

    def listinsert(self, args: tuple[Token, Input, Input]):
        list_ = self.get_list(args[0])
        return Block(
            "data_insertatlist",
            {"INDEX": args[1], "ITEM": args[2]},
            {"LIST": list_},
        )

    def listreplace(self, args: tuple[Token, Input, Input]):
        list_ = self.get_list(args[0])
        return Block(
            "data_replaceitemoflist",
            {"INDEX": args[1], "ITEM": args[2]},
            {"LIST": list_},
        )

    def listreplace_binop(self, opcode: str, args: tuple[Token, Input, Input]):
        list_ = self.get_list(args[0])
        return Block(
            "data_replaceitemoflist",
            {
                "INDEX": args[1],
                "ITEM": Block.from_prototype(
                    reporter_prototypes[opcode],
                    [
                        Block(
                            "data_itemoflist",
                            {"INDEX": args[1]},
                            {"LIST": list_},
                        ),
                        args[2],
                    ],
                ),
            },
            {"LIST": list_},
        )

    def listreplaceadd(self, args: Any):
        return self.listreplace_binop("add", args)

    def listreplacesub(self, args: Any):
        return self.listreplace_binop("sub", args)

    def listreplacemul(self, args: Any):
        return self.listreplace_binop("mul", args)

    def listreplacediv(self, args: Any):
        return self.listreplace_binop("div", args)

    def listreplacemod(self, args: Any):
        return self.listreplace_binop("mod", args)

    def listreplacejoin(self, args: Any):
        return self.listreplace_binop("join", args)

    def listshow(self, args: tuple[Token]):
        list_ = self.get_list(args[0])
        return Block("data_showlist", {}, {"LIST": list_})

    def listhide(self, args: tuple[Token]):
        list_ = self.get_list(args[0])
        return Block("data_hidelist", {}, {"LIST": list_})

    def listitem(self, args: tuple[Token, Input]):
        list_ = self.get_list(args[0])
        return Block("data_itemoflist", {"INDEX": args[1]}, {"LIST": list_})

    def listindex(self, args: tuple[Token, Input]):
        list_ = self.get_list(args[0])
        return Block("data_itemnumoflist", {"ITEM": args[1]}, {"LIST": list_})

    def listcontains(self, args: tuple[Token, Input]):
        list_ = self.get_list(args[0])
        return ConditionBlock(
            "data_listcontainsitem", {"ITEM": args[1]}, {"LIST": list_}
        )

    def listlength(self, args: tuple[Token]):
        list_ = self.get_list(args[0])
        return Block("data_lengthoflist", {}, {"LIST": list_})

    def declr_on(self, args: tuple[Token, Stack]):
        return HatBlock(
            "event_whenbroadcastreceived",
            {},
            {"BROADCAST_OPTION": Variable(str(args[0]), args[0])},
            args[1],
        )

    def declr_onkey(self, args: tuple[Token, Stack]):
        return HatBlock(
            "event_whenkeypressed",
            {},
            {"KEY_OPTION": Variable(str(args[0]), args[0])},
            args[1],
        )

    def declr_onbackdrop(self, args: tuple[Token, Stack]):
        return HatBlock(
            "event_whenbackdropswitchesto",
            {},
            {"BACKDROP_OPTION": Variable(str(args[0]), args[0])},
            args[1],
        )

    def declr_onloudness(self, args: tuple[Input, Stack]):
        return HatBlock(
            "event_whengreaterthan",
            {"VALUE": args[0]},
            {"WHENGREATERTHANMENU": "LOUDNESS"},
            args[1],
        )

    def declr_ontimer(self, args: tuple[Input, Stack]):
        return HatBlock(
            "event_whengreaterthan",
            {"VALUE": args[0]},
            {"WHENGREATERTHANMENU": "TIMER"},
            args[1],
        )

    def declr_onflag(self, args: tuple[Stack]):
        return HatBlock("event_whenflagclicked", {}, {}, args[0])

    def declr_onclick(self, args: tuple[Stack]):
        return HatBlock("event_whenthisspriteclicked", {}, {}, args[0])

    def declr_onclone(self, args: tuple[Stack]):
        return HatBlock("control_start_as_clone", {}, {}, args[0])

    def nop(self, _):
        return Block("control_wait", {"DURATION": "0"}, {})

    def get_variable(self, token: Token | Variable | List):
        if isinstance(token, Variable):
            return token
        if isinstance(token, List):
            msg = "Identifier is not a variable"
            raise TokenError(msg, token.token)

        identifier = self.get_identifier(token)
        if not isinstance(identifier, Variable):
            raise TokenError("Identifier is not a variable", token)
        return identifier

    def get_list(self, token: Token | List | Variable):
        if isinstance(token, List):
            return token
        if isinstance(token, Variable):
            msg = "Identifier is not a list"
            raise TokenError(msg, token.token)
        identifier = self.get_identifier(token)
        if not isinstance(identifier, List):
            raise TokenError("Identifier is not a list", token)
        return identifier
