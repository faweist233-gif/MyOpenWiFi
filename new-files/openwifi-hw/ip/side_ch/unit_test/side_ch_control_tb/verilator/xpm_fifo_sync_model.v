// Functional fall-through (fwft) model of Xilinx xpm_fifo_sync used ONLY for
// iverilog/verilator simulation of side_ch_control. Not used in Vivado xsim
// (Vivado supplies the real primitive). Kept out of the Vivado source set.
`timescale 1ns/1ps
module xpm_fifo_sync #(
  parameter DOUT_RESET_VALUE = "0",
  parameter ECC_MODE = "no_ecc",
  parameter FIFO_MEMORY_TYPE = "auto",
  parameter integer FIFO_READ_LATENCY = 0,
  parameter integer FIFO_WRITE_DEPTH = 512,
  parameter integer FULL_RESET_VALUE = 0,
  parameter integer PROG_EMPTY_THRESH = 10,
  parameter integer PROG_FULL_THRESH = 10,
  parameter integer RD_DATA_COUNT_WIDTH = 10,
  parameter integer READ_DATA_WIDTH = 32,
  parameter READ_MODE = "fwft",
  parameter USE_ADV_FEATURES = "0404",
  parameter integer WAKEUP_TIME = 0,
  parameter integer WRITE_DATA_WIDTH = 32,
  parameter integer WR_DATA_COUNT_WIDTH = 10
)(
  output almost_empty, almost_full, data_valid, dbiterr, empty, full, overflow,
  output prog_empty, prog_full, rd_rst_busy, sbiterr, underflow, wr_ack, wr_rst_busy,
  output [RD_DATA_COUNT_WIDTH-1:0] rd_data_count,
  output [WR_DATA_COUNT_WIDTH-1:0] wr_data_count,
  output [READ_DATA_WIDTH-1:0] dout,
  input [WRITE_DATA_WIDTH-1:0] din,
  input injectdbiterr, injectsbiterr, rd_en, rst, sleep, wr_clk, wr_en
);
  parameter DEPTH = (WRITE_DATA_WIDTH == 32) ? 512 : 64;
  reg [WRITE_DATA_WIDTH-1:0] mem [0:DEPTH-1];
  reg [WRITE_DATA_WIDTH-1:0] last_dout;
  integer wptr, rptr, cnt;

  assign data_valid = (cnt > 0);
  assign empty = (cnt == 0);
  assign full  = (cnt >= DEPTH);
  assign almost_empty = (cnt <= 1);
  assign almost_full  = (cnt >= DEPTH-1);
  assign overflow     = wr_en && (cnt >= DEPTH);
  assign underflow    = rd_en && (cnt == 0);
  assign rd_data_count = cnt;
  assign wr_data_count = cnt;
  assign dbiterr = 0; assign sbiterr = 0; assign prog_empty = empty;
  assign prog_full = full; assign rd_rst_busy = 0; assign wr_rst_busy = 0; assign wr_ack = wr_en;
  // fwft: underflow holds the last valid word (matches real xpm fwft behavior)
  assign dout = (cnt > 0) ? mem[rptr] : last_dout;

  always @(posedge wr_clk) begin
    if (rst) begin
      wptr <= 0; rptr <= 0; cnt <= 0; last_dout <= 0;
    end else begin
      if (wr_en && (cnt < DEPTH)) begin
        mem[wptr] <= din;
        wptr <= (wptr + 1) % DEPTH;
      end
      if (rd_en && (cnt > 0)) begin
        last_dout <= mem[rptr];
        rptr <= (rptr + 1) % DEPTH;
      end
      cnt <= cnt + ((wr_en && (cnt < DEPTH)) ? 1 : 0) - ((rd_en && (cnt > 0)) ? 1 : 0);
    end
  end
endmodule