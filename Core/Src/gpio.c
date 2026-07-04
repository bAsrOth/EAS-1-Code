#include "gpio.h"

void MX_GPIO_Init(void)
{
  GPIO_InitTypeDef GPIO_InitStruct = {0};

  __HAL_RCC_GPIOH_CLK_ENABLE();
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOC_CLK_ENABLE();
  __HAL_RCC_GPIOB_CLK_ENABLE();

  HAL_GPIO_WritePin(CAM_TRIG_GPIO_Port, CAM_TRIG_Pin, GPIO_PIN_RESET);
  HAL_GPIO_WritePin(GPIOC, OFF_LED_Pin	, GPIO_PIN_SET);	//OFF-LED initial einschalten
  HAL_GPIO_WritePin(GPIOC, ON_LED_Pin	, GPIO_PIN_RESET);	//ON-LED initial ausschalten

  GPIO_InitStruct.Pin = CAM_TRIG_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(CAM_TRIG_GPIO_Port, &GPIO_InitStruct);

  GPIO_InitStruct.Pin = OFF_Taster_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(OFF_Taster_GPIO_Port, &GPIO_InitStruct);

  GPIO_InitStruct.Pin = OFF_LED_Pin | ON_LED_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(GPIOC, &GPIO_InitStruct);

  GPIO_InitStruct.Pin = ON_Taster_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(ON_Taster_GPIO_Port, &GPIO_InitStruct);
}
