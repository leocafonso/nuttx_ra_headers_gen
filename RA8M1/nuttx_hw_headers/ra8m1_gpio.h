/****************************************************************************
 * arch/arm/src/ra8m1/hardware/ra8m1_gpio.h
 *
 * Licensed to the Apache Software Foundation (ASF) under one or more
 * contributor license agreements.  See the NOTICE file distributed with
 * this work for additional information regarding copyright ownership.  The
 * ASF licenses this file to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance with the
 * License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
 * WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.  See the
 * License for the specific language governing permissions and limitations
 * under the License.
 *
 ****************************************************************************/

#ifndef __ARCH_ARM_SRC_RA8M1_HARDWARE_RA8M1_GPIO_H
#define __ARCH_ARM_SRC_RA8M1_HARDWARE_RA8M1_GPIO_H

/****************************************************************************
 * Included Files
 ****************************************************************************/

#include <nuttx/config.h>

#include "chip.h"
#include "hardware/ra_memorymap.h"

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

/* Register Offsets **********************************************************/

#define R_PORT0_PCNTR1_OFFSET              0x0000  /* Port Control Register 1 (32-bits) */
#define R_PORT0_PDR_OFFSET                 0x0000  /* Data direction register (16-bits) */
#define R_PORT0_PODR_OFFSET                0x0002  /* Output data register (16-bits) */
#define R_PORT0_PCNTR2_OFFSET              0x0004  /* Port Control Register 2 (32-bits) */
#define R_PORT0_PIDR_OFFSET                0x0004  /* Input data register (16-bits) */
#define R_PORT0_EIDR_OFFSET                0x0006  /* Port Event Input Data register (16-bits) */
#define R_PORT0_PCNTR3_OFFSET              0x0008  /* Port Control Register 3 (32-bits) */
#define R_PORT0_POSR_OFFSET                0x0008  /* Output set register (16-bits) */
#define R_PORT0_PORR_OFFSET                0x000a  /* Output reset register (16-bits) */

/* Register Addresses ********************************************************/

#define R_PORT0_PCNTR1                     (R_PORT0_BASE + R_PORT0_PCNTR1_OFFSET)
#define R_PORT0_PDR                        (R_PORT0_BASE + R_PORT0_PDR_OFFSET)
#define R_PORT0_PODR                       (R_PORT0_BASE + R_PORT0_PODR_OFFSET)
#define R_PORT0_PCNTR2                     (R_PORT0_BASE + R_PORT0_PCNTR2_OFFSET)
#define R_PORT0_PIDR                       (R_PORT0_BASE + R_PORT0_PIDR_OFFSET)
#define R_PORT0_EIDR                       (R_PORT0_BASE + R_PORT0_EIDR_OFFSET)
#define R_PORT0_PCNTR3                     (R_PORT0_BASE + R_PORT0_PCNTR3_OFFSET)
#define R_PORT0_POSR                       (R_PORT0_BASE + R_PORT0_POSR_OFFSET)
#define R_PORT0_PORR                       (R_PORT0_BASE + R_PORT0_PORR_OFFSET)

/* Register Bitfield Definitions *********************************************/

/* Port Control Register 1 (32-bits) *****************************************/

#define R_PORT0_PCNTR1_PDR_SHIFT           (0)
#define R_PORT0_PCNTR1_PDR_MASK            (0xffff)
#  define R_PORT0_PCNTR1_PDR_V0       (0 << R_PORT0_PCNTR1_PDR_SHIFT) /* Input (functions as an input pin) */
#  define R_PORT0_PCNTR1_PDR_V1       (1 << R_PORT0_PCNTR1_PDR_SHIFT) /* Output (functions as an output pin). */
#define R_PORT0_PCNTR1_PODR_SHIFT          (16)
#define R_PORT0_PCNTR1_PODR_MASK           (0xffff)
#  define R_PORT0_PCNTR1_PODR_LOW_OUTPUT (0 << R_PORT0_PCNTR1_PODR_SHIFT) /* Low output */
#  define R_PORT0_PCNTR1_PODR_HIGH_OUTPUT (1 << R_PORT0_PCNTR1_PODR_SHIFT) /* High output. */

/* Data direction register (16-bits) *****************************************/

#define R_PORT0_PDR_PDR_SHIFT              (0)
#define R_PORT0_PDR_PDR_MASK               (0xffff)
#  define R_PORT0_PDR_PDR_V0          (0 << R_PORT0_PDR_PDR_SHIFT) /* Input (functions as an input pin) */
#  define R_PORT0_PDR_PDR_V1          (1 << R_PORT0_PDR_PDR_SHIFT) /* Output (functions as an output pin). */

/* Output data register (16-bits) ********************************************/

#define R_PORT0_PODR_PODR_SHIFT            (0)
#define R_PORT0_PODR_PODR_MASK             (0xffff)
#  define R_PORT0_PODR_PODR_LOW_OUTPUT (0 << R_PORT0_PODR_PODR_SHIFT) /* Low output */
#  define R_PORT0_PODR_PODR_HIGH_OUTPUT (1 << R_PORT0_PODR_PODR_SHIFT) /* High output. */

/* Port Control Register 2 (32-bits) *****************************************/

#define R_PORT0_PCNTR2_PIDR_SHIFT          (0)
#define R_PORT0_PCNTR2_PIDR_MASK           (0xffff)
#  define R_PORT0_PCNTR2_PIDR_LOW_INPUT (0 << R_PORT0_PCNTR2_PIDR_SHIFT) /* Low input */
#  define R_PORT0_PCNTR2_PIDR_HIGH_INPUT (1 << R_PORT0_PCNTR2_PIDR_SHIFT) /* High input. */
#define R_PORT0_PCNTR2_EIDR_SHIFT          (16)
#define R_PORT0_PCNTR2_EIDR_MASK           (0xffff)
#  define R_PORT0_PCNTR2_EIDR_LOW_INPUT (0 << R_PORT0_PCNTR2_EIDR_SHIFT) /* Low input */
#  define R_PORT0_PCNTR2_EIDR_HIGH_INPUT (1 << R_PORT0_PCNTR2_EIDR_SHIFT) /* High input. */

/* Input data register (16-bits) *********************************************/

#define R_PORT0_PIDR_PIDR_SHIFT            (0)
#define R_PORT0_PIDR_PIDR_MASK             (0xffff)
#  define R_PORT0_PIDR_PIDR_LOW_LEVEL (0 << R_PORT0_PIDR_PIDR_SHIFT) /* Low level */
#  define R_PORT0_PIDR_PIDR_HIGH_LEVEL (1 << R_PORT0_PIDR_PIDR_SHIFT) /* high level. */

/* Port Event Input Data register (16-bits) **********************************/

#define R_PORT0_EIDR_EIDR_SHIFT            (0)
#define R_PORT0_EIDR_EIDR_MASK             (0xffff)
#  define R_PORT0_EIDR_EIDR_LOW_INPUT (0 << R_PORT0_EIDR_EIDR_SHIFT) /* Low input */
#  define R_PORT0_EIDR_EIDR_HIGH_INPUT (1 << R_PORT0_EIDR_EIDR_SHIFT) /* High input. */

/* Port Control Register 3 (32-bits) *****************************************/

#define R_PORT0_PCNTR3_POSR_SHIFT          (0)
#define R_PORT0_PCNTR3_POSR_MASK           (0xffff)
#  define R_PORT0_PCNTR3_POSR_NO_AFFECT_TO_OUTPUT (0 << R_PORT0_PCNTR3_POSR_SHIFT) /* No affect to output */
#  define R_PORT0_PCNTR3_POSR_HIGH_OUTPUT (1 << R_PORT0_PCNTR3_POSR_SHIFT) /* High output. */
#define R_PORT0_PCNTR3_PORR_SHIFT          (16)
#define R_PORT0_PCNTR3_PORR_MASK           (0xffff)
#  define R_PORT0_PCNTR3_PORR_NO_AFFECT_TO_OUTPUT (0 << R_PORT0_PCNTR3_PORR_SHIFT) /* No affect to output */
#  define R_PORT0_PCNTR3_PORR_LOW_OUTPUT (1 << R_PORT0_PCNTR3_PORR_SHIFT) /* Low output. */

/* Output set register (16-bits) *********************************************/

#define R_PORT0_POSR_POSR_SHIFT            (0)
#define R_PORT0_POSR_POSR_MASK             (0xffff)
#  define R_PORT0_POSR_POSR_NO_AFFECT_TO_OUTPUT (0 << R_PORT0_POSR_POSR_SHIFT) /* No affect to output */
#  define R_PORT0_POSR_POSR_HIGH_OUTPUT (1 << R_PORT0_POSR_POSR_SHIFT) /* High output. */

/* Output reset register (16-bits) *******************************************/

#define R_PORT0_PORR_PORR_SHIFT            (0)
#define R_PORT0_PORR_PORR_MASK             (0xffff)
#  define R_PORT0_PORR_PORR_NO_AFFECT_TO_OUTPUT (0 << R_PORT0_PORR_PORR_SHIFT) /* No affect to output */
#  define R_PORT0_PORR_PORR_LOW_OUTPUT (1 << R_PORT0_PORR_PORR_SHIFT) /* Low output. */

/****************************************************************************
 * Public Types
 ****************************************************************************/

/****************************************************************************
 * Public Data
 ****************************************************************************/

/****************************************************************************
 * Public Functions Prototypes
 ****************************************************************************/

#endif /* __ARCH_ARM_SRC_RA8M1_HARDWARE_RA8M1_GPIO_H */
