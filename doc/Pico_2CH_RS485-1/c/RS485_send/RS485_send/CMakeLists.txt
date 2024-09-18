add_executable(RS485_send
       RS485_send.c
        )

# Pull in our pico_stdlib which pulls in commonly used features
target_link_libraries(RS485_send pico_stdlib)

# enable usb output, disable uart output
pico_enable_stdio_usb(RS485_send 1)
pico_enable_stdio_uart(RS485_send 0)

# create map/bin/hex file etc.
pico_add_extra_outputs(RS485_send)

# add url via pico_set_program_url
example_auto_set_url(RS485_send)
