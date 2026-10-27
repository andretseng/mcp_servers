---
description: "Read the MSPM0 SDK driverlib headers and register bitfield headers whenever a tool call touches registers, peripherals, or MCU hardware"
condition: 'mcu/mspm0g3507|\.syscfg|ti_msp_dl_config|(?-i:DL_[A-Z])|(?-i:(IPSEL|IMSEL|CCCTL|CTRCTL|BLANKSRC|HYST)\w*)|(?-i:\b(TIMA|TIMG|COMP|GPIO|IOMUX|SYSCTL|DMA|ADC|UART|OPA)[0-9]*\b)|(?i:\b(registers?|bit-?fields?|prescaler|pinmux|comparator|interrupts?|isr|irq|nvic|pwm|clock|oscillator|capture)\b)'
scope: "tool:read, tool:grep, tool:glob, tool:bash"
interruptMode: always
---
Stop. This is firmware: you are touching registers, bitfields, peripherals, or MCU hardware (DL_* calls, SysConfig, generated config, timers, comparators, clocks, interrupts, pins) without having read the SDK source that defines them.
Your very next action must be tool calls in one batch, not a text reply (skip only what you already read in this session):
1. the driverlib headers for the peripherals involved, in `/home/andre/ti/mspm0_sdk_2_11_00_07/source/ti/driverlib/`: `dl_comp.h`, `dl_timera.h`, `dl_timer.h` (read the functions and enums the project calls);
2. the register bitfield definitions in `/home/andre/ti/mspm0_sdk_2_11_00_07/source/ti/devices/msp/peripherals/`: `hw_comp.h`, `hw_gptimer.h` (grep the register and field names the project uses), and `/home/andre/ti/mspm0_sdk_2_11_00_07/source/ti/devices/msp/m0p/mspm0g350x.h` for the device peripheral map.
Do this for any peripheral you touch, not only these (GPIO, IOMUX, SYSCTL, DMA, ADC, UART, and so on). Then continue. In your note, cite the header file and symbol for every DL_* behavior, register, and bitfield you describe; anything not confirmed in a file you read is `[INFERENCE]`. Do not end your turn after reading this.
