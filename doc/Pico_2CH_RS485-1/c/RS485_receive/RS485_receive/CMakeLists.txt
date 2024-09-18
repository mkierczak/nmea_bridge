add_executable(RS485_receive
       RS485_receive.c
        )

# Pull in our pico_stdlib which pulls in commonly used features
target_link_libraries(RS485_receive pico_stdlib)


# enable usb output, disable uart output
pico_enable_stdio_usb(RS485_receive 1)
pico_enable_stdio_uart(RS485_receive 1)

# create map/bin/hex file etc.
pico_add_extra_outputs(RS485_receive)

# add url via pico_set_program_url
example_auto_set_url(RS485_receive)
