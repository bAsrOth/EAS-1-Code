/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Main program body
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2026 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */
#include "main.h"
#include "clock.h"
#include "timer.h"
#include "gpio.h"
#include <stdint.h>

// Enum für die LED-Kanäle (DIM1–DIM8)
typedef enum {
    DIM1 = 0,
    DIM2,
    DIM3,
    DIM4,
    DIM5,
    DIM6,
    DIM7,
    DIM8
} DIM_Channel;

void LED_SetDutyCycle(DIM_Channel channel, float duty);

volatile unsigned int sys_tick  = 0;   // wird vom TIM3-IRQ jede ms inkrementiert (0–29)
volatile unsigned int system_on = 0;   // 0 = LED-Ansteuerung aus, 1 = ein
DIM_Channel current_ch = DIM8;         // aktuell aktiver Kanal (DIM8 → erster Taktschritt der Systemclock ergibt DIM1)

static unsigned int on_counter  = 0;   // Debounce-Zähler ON-Taster
static unsigned int off_counter = 0;   // Debounce-Zähler OFF-Taster

int main(void)
{
  HAL_Init();
  SystemClock_Config();

  MX_GPIO_Init();
  MX_TIM3_Init();
  MX_TIM4_Init();
  MX_TIM8_Init();

  HAL_TIM_Base_Start_IT(&htim3);

  // Duty Cycle pro Kanal (0.0 = 0%, 1.0 = 100%)
  static const float duty_table[8] = {
      0.13f,  	// DIM1 mit 1.5A
      0.13f,  	// DIM2 mit 1.5A
      0.13f,  	// DIM3 mit 1.5A
      0.2f, 	// DIM4 mit 1A
      0.2f, 	// DIM5 mit 1A
      0.13f,  	// DIM6 mit 1.5A
      0.13f,  	// DIM7 mit 1.5A
      0.13f,  	// DIM8 mit 1.5A
  };

  // Alle Kanäle auf 0 -> LEDs aus bevor Loop startet
  for (DIM_Channel ch = DIM1; ch <= DIM8; ch++)
      LED_SetDutyCycle(ch, 0.0f);

  // Alle Kanäle starten - Duty 0.0 hält Pin auf LOW (LED aus)
  HAL_TIM_PWM_Start(&htim8, TIM_CHANNEL_4);  // DIM1
  HAL_TIM_PWM_Start(&htim8, TIM_CHANNEL_3);  // DIM2
  HAL_TIM_PWM_Start(&htim8, TIM_CHANNEL_2);  // DIM3
  HAL_TIM_PWM_Start(&htim8, TIM_CHANNEL_1);  // DIM4
  HAL_TIM_PWM_Start(&htim4, TIM_CHANNEL_1);  // DIM5
  HAL_TIM_PWM_Start(&htim4, TIM_CHANNEL_2);  // DIM6
  HAL_TIM_PWM_Start(&htim4, TIM_CHANNEL_3);  // DIM7
  HAL_TIM_PWM_Start(&htim4, TIM_CHANNEL_4);  // DIM8

  static unsigned int last_tick = 8; //Last Tick muss intial unterschiedlich vom System-Tick sein

  while (1)
  {
      // Nur einmal pro ms ausführen
      if (sys_tick != last_tick)
      {
          last_tick = sys_tick; //temp-Tick variable

          if (system_on == 1) //ON-Taster wurde gedrückt
          {
              if (sys_tick == 0)   // Neuer Frame: nächsten Kanal wählen
              {
                  if (current_ch < DIM8) current_ch++; //Wenn nicht Kanal 8, dann Kanal hochzählen
                  else current_ch = DIM1;
              }

              if (sys_tick == 1)   // ms 1: Kanal einschalten mit Duty Cycle
              {
                  LED_SetDutyCycle(current_ch, duty_table[current_ch]);
              }

              if (sys_tick == 2)   // ms 2: Kamera-Trigger HIGH
              {
                  HAL_GPIO_WritePin(CAM_TRIG_GPIO_Port, CAM_TRIG_Pin, GPIO_PIN_SET);
              }

              if (sys_tick == 3)   // ms 3: Kamera-Trigger LOW (1ms Puls)
              {
                  HAL_GPIO_WritePin(CAM_TRIG_GPIO_Port, CAM_TRIG_Pin, GPIO_PIN_RESET);
              }

              if (sys_tick == 11)  // ms 11: Kanal ausschalten
              {
                  LED_SetDutyCycle(current_ch, 0.0f);
              }
          }
      }
  }
}


/**
 * @brief  TIM3 Interrupt Handler – wird alle 1 ms aufgerufen.
 *         Zählt sys_tick bis 30 als Zeitgeber.
 *         Übernimmt Debouning der Taster
 */
void TIM3_IRQHandler(void)
{
    if (TIM3->SR & TIM_SR_UIF)        // Update Interrupt Flag gesetzt?
    {
        TIM3->SR &= ~TIM_SR_UIF;      // Flag manuell löschen (sonst dauerhafter Re-Eintritt)
        if (sys_tick >= 29)
            sys_tick = 0;
        else
            sys_tick++;

        // Debouncing des ON-Taster
        if (HAL_GPIO_ReadPin(ON_Taster_GPIO_Port, ON_Taster_Pin) == GPIO_PIN_RESET) // Taster-Pin low?
            on_counter++;
        else
            on_counter = 0;

        if (on_counter == 20)	//20ms Debouncing Zeit abgelaufen
        {
            system_on = 1;		//System anschalten
            current_ch = DIM8;  // Sequenz immer bei DIM1 starten
            HAL_GPIO_WritePin(ON_LED_GPIO_Port,  ON_LED_Pin,  GPIO_PIN_SET);
            HAL_GPIO_WritePin(OFF_LED_GPIO_Port, OFF_LED_Pin, GPIO_PIN_RESET);
        }

        // Debouncingd des OFF-Taster
        if (HAL_GPIO_ReadPin(OFF_Taster_GPIO_Port, OFF_Taster_Pin) == GPIO_PIN_RESET)
            off_counter++;
        else
            off_counter = 0;

        if (off_counter == 20)
        {
            system_on = 0;
            HAL_GPIO_WritePin(OFF_LED_GPIO_Port, OFF_LED_Pin, GPIO_PIN_SET);		//LEDs entsprechend setzen
            HAL_GPIO_WritePin(ON_LED_GPIO_Port,  ON_LED_Pin,  GPIO_PIN_RESET);
            HAL_GPIO_WritePin(CAM_TRIG_GPIO_Port, CAM_TRIG_Pin, GPIO_PIN_RESET); 	//Kamera Trigger Pin auf 0
        }
    }
}

/**
 * @brief  Setzt den Duty Cycle eines LED-Kanals.
 *         Der Wert wird direkt ins Compare-Register (CCRx) geschrieben
 *         und ist sofort aktiv – kein Neustart des Timers nötig.
 *
 * @param  channel  Kanal (DIM1–DIM8)
 * @param  duty     Duty Cycle als Float: 0.0 = 0%, 1.0 = 100%
 *                  Werte außerhalb [0.0, 1.0] werden automatisch begrenzt.
 */
void LED_SetDutyCycle(DIM_Channel channel, float duty)
{
    if (duty < 0.0f) duty = 0.0f; //Grenzen des Inputwerts überprüfen
    if (duty > 1.0f) duty = 1.0f;

    uint32_t pulse = (uint32_t)(duty * 959.0f);  // ARR = 959 → 100 kHz

    switch (channel)
    {
        case DIM1: TIM8->CCR4 = pulse; break;  	// PC9 → TIM8_CH4
        case DIM2: TIM8->CCR3 = pulse; break;  	// PC8 → TIM8_CH3
        case DIM3: TIM8->CCR2 = pulse; break;  	// PC7 → TIM8_CH2
        case DIM4: TIM8->CCR1 = pulse; break;  	// PC6 → TIM8_CH1
        case DIM5: TIM4->CCR1 = pulse; break;
        case DIM6: TIM4->CCR2 = pulse; break;
        case DIM7: TIM4->CCR3 = pulse; break;
        case DIM8: TIM4->CCR4 = pulse; break;
    }
}

void Error_Handler(void)
{
  __disable_irq();
  while (1)
  {
  }
}

#ifdef USE_FULL_ASSERT
void assert_failed(uint8_t *file, uint32_t line)
{
}
#endif /* USE_FULL_ASSERT */

