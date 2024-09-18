/************************************************************************/
/**@name：		RS485_irq.c
 **@auther:		waveshare team
 **@info:		This code has configured a serial port of Pico to connect to our PICO-2CH-RS485, 
			    It receives the data you send it through interrupts and returns the received data to the sender.	
 **/
/************************************************************************/
#include "pico/stdlib.h"
#include "hardware/uart.h"
#include "hardware/irq.h"

/// \tag::uart_advanced[]
#define UART_ID0 uart0
#define UART_ID1 uart1
#define BAUD_RATE 115200
#define DATA_BITS 8
#define STOP_BITS 1
#define PARITY    UART_PARITY_NONE

// We are using pins 0 and 1, but see the GPIO function select table in the
// datasheet for information on which other pins can be used.
#define UART_TX_PIN0 0
#define UART_RX_PIN0 1
#define UART_TX_PIN1 4
#define UART_RX_PIN1 5


uint8_t buffter[100] = {0} ;
uint8_t cnt = 0,cnt1 = 0;
uint8_t f=0;
// RX interrupt handler
void on_uart_rx0() 
{
    f = 1;
    while (uart_is_readable(UART_ID0)) 
    {
        buffter[cnt] = uart_getc(UART_ID0);
        cnt++;
    }
    
}

void on_uart_rx1() 
{
    f = 1;
    while (uart_is_readable(UART_ID1)) 
    {
        buffter[cnt1] = uart_getc(UART_ID1);
        cnt1++;
    }
}

int main(void)
{
  // Set up our UART with a basic baud rate.
    uart_init(UART_ID0, 115200);
    uart_init(UART_ID1, 115200);
    // Set the TX and RX pins by using the function select on the GPIO
    // Set datasheet for more information on function select
    gpio_set_function(UART_TX_PIN0, GPIO_FUNC_UART);
    gpio_set_function(UART_RX_PIN0, GPIO_FUNC_UART);
    gpio_set_function(UART_TX_PIN1, GPIO_FUNC_UART);
    gpio_set_function(UART_RX_PIN1, GPIO_FUNC_UART);
    // Actually, we want a different speed
    // The call will return the actual baud rate selected, which will be as close as
    // possible to that requested
    //int actual = uart_set_baudrate(UART_ID, BAUD_RATE);

    // Set UART flow control CTS/RTS, we don't want these, so turn them off
    uart_set_hw_flow(UART_ID0, false, false);
    uart_set_hw_flow(UART_ID1, false, false);
    // Set our data format
    uart_set_format(UART_ID0, DATA_BITS, STOP_BITS, PARITY);
    uart_set_format(UART_ID1, DATA_BITS, STOP_BITS, PARITY);
    // Turn off FIFO's - we want to do this character by character
    uart_set_fifo_enabled(UART_ID0, false);
    uart_set_fifo_enabled(UART_ID1, false);
    // Set up a RX interrupt
    // We need to set up the handler first
    // Select correct interrupt for the UART we are using
    // int UART_IRQ = UART_ID == uart0 ? UART0_IRQ : UART1_IRQ;
    // int UART_IRQ = UART_ID == uart0 ? UART0_IRQ : UART1_IRQ;
    // And set up and enable the interrupt handlers
    irq_set_exclusive_handler(UART0_IRQ, on_uart_rx0);
    irq_set_enabled(UART0_IRQ, true);
    irq_set_exclusive_handler(UART1_IRQ, on_uart_rx1);
    irq_set_enabled(UART1_IRQ, true);
    // Now enable the UART to send interrupts - RX only
    uart_set_irq_enables(UART_ID0, true, false);
    uart_set_irq_enables(UART_ID1, true, false);
    // OK, all set up.
    // Lets send a basic string out, and then run a loop and wait for RX interrupts
    // The handler will count them, but also reflect the incoming data back with a slight change!
    uart_puts(UART_ID0, "\nHello, uart0 interrupts\n");
    uart_puts(UART_ID1, "\nHello, uart1 interrupts\n");
    while(1)
    {

        if((cnt != 0)&&(f == 0))
        {
            sleep_ms(10);
            if((cnt != 0)&&(f == 0))
            {
                for(uint8_t i=0 ; i<cnt ; i++)
                {
                    uart_putc(UART_ID0,buffter[i]);
                }
                cnt = 0;
            }   
        }

        if((cnt1 != 0)&&(f == 0))
        {
            sleep_ms(10);
            if((cnt1 != 0)&&(f == 0))
            {
                for(uint8_t i=0 ; i<cnt1 ; i++)
                {
                    uart_putc(UART_ID1,buffter[i]);
                }
                cnt1 = 0;
            }      
        }
        
        f = 0;
    
    }
    
}
        
