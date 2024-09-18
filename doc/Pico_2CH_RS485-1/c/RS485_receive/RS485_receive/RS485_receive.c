/************************************************************************/
/**@name：		RS485_receive.c
 **@auther:		waveshare team
 **@info:		This code configues a serial port of the Pico to connect to our PICO-2CH-RS485,
				and when the serial port receives any data it will return to them.
 **/
/************************************************************************/
#include <stdio.h>
#include "pico/stdlib.h"
#include "hardware/uart.h"
#include "hardware/irq.h"

#define UART_ID uart0
//#define UART_ID uart1
#define BAUD_RATE 115200
#define DATA_BITS 8
#define STOP_BITS 1
#define PARITY    UART_PARITY_NONE

#define UART_TX_PIN 0
#define UART_RX_PIN 1
// #define UART_TX_PIN 4
// #define UART_RX_PIN 5

int main() {
	
	uint8_t flag=1;
	stdio_init_all();
    uart_init(UART_ID, 115200);
    gpio_set_function(UART_TX_PIN, GPIO_FUNC_UART);
    gpio_set_function(UART_RX_PIN, GPIO_FUNC_UART);
    uart_set_baudrate(UART_ID, BAUD_RATE);
    uart_set_hw_flow(UART_ID, false, false);
    uart_set_format(UART_ID, DATA_BITS, STOP_BITS, PARITY);
    uart_set_fifo_enabled(UART_ID,true);
    uart_puts(UART_ID, "RS485 receive test...\r\n");
	sleep_ms(100);

	while(1)
	{
		while(uart_is_readable(UART_ID))
		{
			uint8_t ch;
			//ch = uart_getc(UART_ID);
			uart_read_blocking (uart0,&ch,1);
			if(flag==1)
			{
				sleep_ms(50);
				flag=0;
			}
			//sleep_ms(1);
			//uart_putc(UART_ID, ch);
			uart_write_blocking(uart0,&ch,1);
			if(uart_is_readable(UART_ID) == 0)
			{
				uart_puts(UART_ID, "\r\n");
				flag=1;
			}
		}			
	}
	
	

}


