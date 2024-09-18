add_executable(RS485_irq
       RS485_irq.c
        )

# Pull in our pico_stdlib which pulls in commonly used features
target_link_libraries(RS485_irq pico_stdlib hardware_uart)

# create map/bin/hex file etc.
pico_add_extra_outputs(RS485_irq)

# add url via pico_set_program_url
example_auto_set_url(RS485_irq)
