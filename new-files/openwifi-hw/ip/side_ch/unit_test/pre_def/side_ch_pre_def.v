// E316 board config: less BRAM -> 4096-deep m_axis FIFO.
// NOTE: current side_ch_control.v controls FIFO depth via parameter MAX_NUM_DMA_SYMBOL
// (the tb passes 4096 explicitly), so this `define is no longer referenced by the RTL.
// Kept because side_ch_control.v does `include "side_ch_pre_def.v"` (the file must exist).
`define SIDE_CH_LESS_BRAM 1
