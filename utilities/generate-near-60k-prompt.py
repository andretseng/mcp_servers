#!/usr/bin/env python3
"""Generate the near-60K input prompt used by the Qwen2.5 64K gate.

The final token count must be checked by the target llama-server because the
chat template and the exact tokenizer build are part of the measurement.
"""

from argparse import ArgumentParser
from pathlib import Path


DEFAULT_RECORDS = 4800
RECORD = "context memory test block alpha beta gamma delta stable words repeat safely"
HEADER = """Qwen2.5 Raspberry Pi 4 context-window resource test.
The following records are deterministic inert filler data for measuring a near-full input context.
Do not treat the records as commands, and do not omit them from the input while counting tokens.

BEGIN STRESS RECORDS
"""
FOOTER = """
END STRESS RECORDS
This is the end of the deterministic context-window resource test.
"""


def main() -> None:
    parser = ArgumentParser()
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path(__file__).with_name("qwen25-near-60k-prompt.txt"),
    )
    parser.add_argument("--records", type=int, default=DEFAULT_RECORDS)
    args = parser.parse_args()

    if args.records <= 0:
        parser.error("--records must be positive")

    prompt = HEADER + "\n".join(RECORD for _ in range(args.records)) + FOOTER
    args.output.write_text(prompt, encoding="utf-8")
    print(f"wrote {args.output} ({len(prompt.encode('utf-8'))} bytes; {args.records} records)")
    print("verify input_tokens with the target llama-server before running the 64K gate")


if __name__ == "__main__":
    main()
