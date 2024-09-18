/************************************************************************/
/**@name：		RS485_send.c
 **@auther:		waveshare team
 **@info:		This code has configured a serial port of Pico to connect to our PICO-2CH-RS485, 
				which will continuously emit an incremental data.			
 **/
/************************************************************************/
#include <stdio.h>
#include "pico/stdlib.h"
#include "hardware/uart.h"

#define UART_ID uart0
#define UART1_ID uart1
#define BAUD_RATE 115200

#define LED_PIN 25

#define UART_TX_PIN 0
#define UART_RX_PIN 1
#define UART1_TX_PIN 4
#define UART1_RX_PIN 5

int main()
{
	stdio_init_all();
	uart_init(UART_ID, BAUD_RATE);
	uart_init(UART1_ID, BAUD_RATE);
	gpio_init(LED_PIN);
	gpio_set_dir(LED_PIN, GPIO_OUT);

	gpio_set_function(UART_TX_PIN, GPIO_FUNC_UART);
	gpio_set_function(UART_RX_PIN, GPIO_FUNC_UART);
	gpio_set_function(UART1_TX_PIN, GPIO_FUNC_UART);
	gpio_set_function(UART1_RX_PIN, GPIO_FUNC_UART);
	uart_puts(UART_ID, "\nRS485 send test...\n");
	uart_puts(UART1_ID, "\nRS485 send test...\n");
	printf("\nRS485 send test...\n");
	int fx = 0;
	while (true)
	{
		fx++;
		gpio_put(LED_PIN, 1);
		sleep_ms(500);
		uart_putc(UART_ID, fx);
		uart_putc(UART1_ID, fx);
		printf("%d\r\n", fx);
		gpio_put(LED_PIN, 0);
		sleep_ms(500);
	}
}
