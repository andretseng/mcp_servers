---
description: "Read the SDK headers before you describe or reason about registers and bitfields"
condition: '(?-i:DL_[A-Z])|(?-i:(IPSEL|IMSEL|CCCTL|CTRCTL|BLANKSRC)\w*)|(?i:\b(registers?|bit-?fields?|prescaler)\b)'
scope: "text, thinking"
interruptMode: always
---
Stop. You are describing or reasoning about registers or bitfields. If you have not yet read the SDK source for them in this session, your very next action must be tool calls, not text: read the relevant driverlib headers in `/home/andre/ti/mspm0_sdk_2_11_00_07/source/ti/driverlib/` (`dl_comp.h`, `dl_timera.h`, `dl_timer.h`) and the bitfield headers in `/home/andre/ti/mspm0_sdk_2_11_00_07/source/ti/devices/msp/peripherals/` (`hw_comp.h`, `hw_gptimer.h`), plus `.../devices/msp/m0p/mspm0g350x.h`. Claims about registers that you did not confirm in a header are `[INFERENCE]`. Do not end your turn after reading this.
