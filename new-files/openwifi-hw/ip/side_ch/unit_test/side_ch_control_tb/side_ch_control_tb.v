// Behavioral testbench for side_ch_control combined mode (CSI + preamble IQ, route A).
// Run under Icarus/Verilator with the functional xpm_fifo_sync model in ../verilator/.
// In Vivado xsim use the real xpm primitive instead of the model.
//
// Scenarios (each = one "region" of SPACING samples in test_vec/data_in.txt):
//  R0/R1  matched addrs+FC          -> expect one RECORD_LEN record each
//  R2/R3  FC mismatch (match_cfg[0]) -> expect NO record; R3 window re-locked by R4
//  R4     matched after R3          -> expect record (window ownership = R4)
//  R5     14-byte short frame (no addr2) -> expect record (CONDITION1->DONE path)
//  R6     m_axis near-full          -> expect WHOLE record dropped (no partial)
//  R7     matched recovery          -> expect record
// Records: [IQ: TSF + iq_len samples][CSI: TSF + phase_offset + 56 CSI]

`timescale 1ns/1ps

//`define DEBUG_MON

module side_ch_control_tb;

  localparam integer IQ_DATA_WIDTH     = 16;
  localparam integer TSF_TIMER_WIDTH   = 64;
  localparam integer C_S_AXI_DATA_WIDTH= 32;
  localparam integer C_S_AXIS_TDATA_W  = 64;
  localparam integer MAX_NUM_DMA_SYMBOL= 4096;      // E316 SIDE_CH_LESS_BRAM
  localparam integer MAX_BIT_NUM_DMA   = 13;        // clogb2(4096) = 13 (12 会截断 >4095 的 iq_len)

  localparam integer IQ_LEN        = 440;
  localparam integer PRE_TRIG_OK   = 300;           // C1 for THIS testbench (self-consistent with gen_preamble.py);
                                                     // board-calibrated C1 = 256 (see 部署文档 §5.1, e316_deploy.sh)
  localparam integer NUM_EQ        = 0;
  localparam integer CSI_BLK       = 2 + 56;
  localparam integer RECORD_LEN    = (1+IQ_LEN) + CSI_BLK;         // 499

  localparam integer SPACING        = 2100;
  localparam integer PREAMBLE_LEN   = 440;
  localparam integer PREAMBLE_START = 300;
  localparam integer HDR_REL        = 400;
  localparam integer FC_REL         = 440;
  localparam integer A1_REL         = 460;
  localparam integer A2_REL         = 480;
  localparam integer EQ_START       = 520;
  localparam integer FCS_REL        = 2000;
  localparam integer N_REGIONS      = 10;

  // ---------------- IO ----------------
  reg clk = 0; always #5 clk = ~clk;    // 100 MHz (clk 必须初始化，否则 ~x = x 时钟不翻转)
  reg rstn;

  reg [7:0] gpio_status;
  reg signed [10:0] rssi_half_db;
  reg [63:0] tsf_runtime_val;
  reg [31:0] iq0, iq1;
  reg iq_strobe;
  reg demod_is_ongoing;
  reg ofdm_symbol_eq_out_pulse;
  reg long_preamble_detected;
  reg short_preamble_detected;
  reg ht_unsupport;
  reg [7:0] pkt_rate;
  reg [15:0] pkt_len;
  reg [31:0] csi;
  reg csi_valid;
  reg signed [31:0] phase_offset_taken;
  reg [31:0] equalizer;
  reg equalizer_valid;
  reg pkt_header_valid;
  reg pkt_header_valid_strobe;
  reg [1:0] phy_type;
  reg [3:0] tx_control_state;
  reg [31:0] FC_DI;
  reg FC_DI_valid;
  reg [47:0] addr1, addr2, addr3;
  reg addr1_valid, addr2_valid, addr3_valid;
  reg fcs_in_strobe, fcs_ok;
  reg block_rx_dma_to_ps, block_rx_dma_to_ps_valid, ch_idle_final;
  reg phy_tx_start, tx_pkt_need_ack, phy_tx_started, phy_tx_done;
  reg tx_bb_is_ongoing, tx_rf_is_ongoing, tx_pkt_iq_to_dac_ongoing, retrans_in_progress;
  reg slv_reg_wren_signal; reg [4:0] axi_awaddr_core;
  reg iq_capture; reg [1:0] iq_capture_cfg; reg csi_iq_combined;
  reg [4:0] iq_trigger_select; reg iq_trigger_free_run_flag; reg [1:0] iq_source_select;
  reg disable_tx_pkt_need_ack_check; reg [2:0] PPDU_FORMAT_target;
  reg [15:0] rssi_or_iq_th; reg [6:0] gain_th;
  reg [MAX_BIT_NUM_DMA-1:0] pre_trigger_len, iq_len_target;
  reg [3:0] tx_control_state_target; reg [1:0] phy_type_target;
  reg [15:0] FC_target; reg [31:0] addr1_target, addr2_target;
  reg [3:0] match_cfg; reg [3:0] num_eq;
  reg [1:0] m_axis_start_mode; reg m_axis_start_ext_trigger;
  reg [63:0] data_to_pl; reg S_AXIS_TVALID, S_AXIS_TLAST;
  reg [MAX_BIT_NUM_DMA-1:0] s_axis_data_count; reg emptyn_to_pl;
  reg M_AXIS_TVALID, M_AXIS_TLAST;
  wire pl_ask_data;
  wire [MAX_BIT_NUM_DMA-1:0] m_axis_data_count;
  wire fulln_to_pl;
  wire m_axis_start_1trans;
  wire [63:0] data_to_ps;
  wire data_to_ps_valid;
  wire [31:0] MAX_NUM_DMA_SYMBOL_UDP_debug, MAX_NUM_DMA_SYMBOL_debug;

  // ---------------- FIFO count model (no drain; seedable for near-full test) ----------------
  reg [MAX_BIT_NUM_DMA-1:0] m_axis_fifo_count;
  reg [MAX_BIT_NUM_DMA-1:0] count_seed;
  reg seed_count;
  reg clear_count;

  // ---------------- m_axis capture ----------------
  reg [63:0] out_mem [0:(MAX_NUM_DMA_SYMBOL*2)-1];
  integer out_cnt;

  // ---------------- test vector memory ----------------
  integer fd, read_ret, file_len;
  reg signed [15:0] file_i [0:(N_REGIONS*SPACING)-1];
  reg signed [15:0] file_q [0:(N_REGIONS*SPACING)-1];

  // ---------------- global sample driver ----------------
  integer gsamp;
  integer region;
  integer rel;
  reg [2:0] sCnt;
  reg done_driving;

  // per-region config tables (indexed by region r = gsamp/SPACING)
  integer pkt_len_tab [0:N_REGIONS-1];
  integer fc_tab      [0:N_REGIONS-1];
  integer trig_tab    [0:N_REGIONS-1];  // pre_trigger_len per region (C1 self-check)
  reg seed_tab        [0:N_REGIONS-1];   // near-full seed at this region's commit time
  reg clear_tab       [0:N_REGIONS-1];   // clear fifo-count at region start (after near-full test)
  localparam integer TSF_PKT_BASE = 1000;

  // ---------------- assertion state ----------------
  integer errors;
  integer expected_total;      // records expected by this point
  integer record_start;        // out_mem index of current record
  integer passed_records;
  integer last_record_end;

  side_ch_control #(
    .TSF_TIMER_WIDTH(TSF_TIMER_WIDTH),
    .GPIO_STATUS_WIDTH(8),
    .RSSI_HALF_DB_WIDTH(11),
    .C_S_AXI_DATA_WIDTH(C_S_AXI_DATA_WIDTH),
    .IQ_DATA_WIDTH(IQ_DATA_WIDTH),
    .C_S_AXIS_TDATA_WIDTH(C_S_AXIS_TDATA_W),
    .MAX_NUM_DMA_SYMBOL(MAX_NUM_DMA_SYMBOL),
    .MAX_BIT_NUM_DMA_SYMBOL(MAX_BIT_NUM_DMA)
  ) dut (
    .clk(clk), .rstn(rstn),
    .gpio_status(gpio_status), .rssi_half_db(rssi_half_db), .tsf_runtime_val(tsf_runtime_val),
    .openofdm_tx_iq0(32'b0), .openofdm_tx_iq1(32'b0), .openofdm_tx_iq_valid(1'b0),
    .tx_intf_iq0(32'b0), .tx_intf_iq1(32'b0), .tx_intf_iq_valid(1'b0),
    .iq0(iq0), .iq1(iq1), .iq_strobe(iq_strobe),
    .demod_is_ongoing(demod_is_ongoing), .ofdm_symbol_eq_out_pulse(ofdm_symbol_eq_out_pulse),
    .long_preamble_detected(long_preamble_detected), .short_preamble_detected(short_preamble_detected),
    .ht_unsupport(ht_unsupport), .pkt_rate(pkt_rate), .pkt_len(pkt_len),
    .csi(csi), .csi_valid(csi_valid), .phase_offset_taken(phase_offset_taken),
    .equalizer(equalizer), .equalizer_valid(equalizer_valid),
    .pkt_header_valid(pkt_header_valid), .pkt_header_valid_strobe(pkt_header_valid_strobe),
    .phy_type(phy_type),
    .tx_control_state(tx_control_state),
    .FC_DI(FC_DI), .FC_DI_valid(FC_DI_valid),
    .addr1(addr1), .addr1_valid(addr1_valid), .addr2(addr2), .addr2_valid(addr2_valid),
    .addr3(addr3), .addr3_valid(addr3_valid),
    .fcs_in_strobe(fcs_in_strobe), .fcs_ok(fcs_ok),
    .block_rx_dma_to_ps(block_rx_dma_to_ps), .block_rx_dma_to_ps_valid(block_rx_dma_to_ps_valid),
    .ch_idle_final(ch_idle_final),
    .phy_tx_start(phy_tx_start), .tx_pkt_need_ack(tx_pkt_need_ack),
    .phy_tx_started(phy_tx_started), .phy_tx_done(phy_tx_done),
    .tx_bb_is_ongoing(tx_bb_is_ongoing), .tx_rf_is_ongoing(tx_rf_is_ongoing),
    .tx_pkt_iq_to_dac_ongoing(tx_pkt_iq_to_dac_ongoing), .retrans_in_progress(retrans_in_progress),
    .slv_reg_wren_signal(slv_reg_wren_signal), .axi_awaddr_core(axi_awaddr_core),
    .iq_capture(iq_capture), .iq_capture_cfg(iq_capture_cfg), .csi_iq_combined(csi_iq_combined),
    .iq_trigger_select(iq_trigger_select), .iq_trigger_free_run_flag(iq_trigger_free_run_flag),
    .iq_source_select(iq_source_select), .disable_tx_pkt_need_ack_check(disable_tx_pkt_need_ack_check),
    .PPDU_FORMAT_target(PPDU_FORMAT_target), .rssi_or_iq_th(rssi_or_iq_th), .gain_th(gain_th),
    .pre_trigger_len(pre_trigger_len), .iq_len_target(iq_len_target),
    .tx_control_state_target(tx_control_state_target), .phy_type_target(phy_type_target),
    .FC_target(FC_target), .addr1_target(addr1_target), .addr2_target(addr2_target),
    .match_cfg(match_cfg), .num_eq(num_eq), .m_axis_start_mode(m_axis_start_mode),
    .m_axis_start_ext_trigger(m_axis_start_ext_trigger),
    .data_to_pl(data_to_pl), .pl_ask_data(pl_ask_data), .s_axis_data_count(s_axis_data_count),
    .emptyn_to_pl(emptyn_to_pl), .S_AXIS_TVALID(S_AXIS_TVALID), .S_AXIS_TLAST(S_AXIS_TLAST),
    .m_axis_start_1trans(m_axis_start_1trans),
    .data_to_ps(data_to_ps), .data_to_ps_valid(data_to_ps_valid),
    .m_axis_data_count(m_axis_data_count), .fulln_to_pl(fulln_to_pl),
    .MAX_NUM_DMA_SYMBOL_UDP_debug(MAX_NUM_DMA_SYMBOL_UDP_debug),
    .MAX_NUM_DMA_SYMBOL_debug(MAX_NUM_DMA_SYMBOL_debug),
    .M_AXIS_TVALID(M_AXIS_TVALID), .M_AXIS_TLAST(M_AXIS_TLAST)
  );

  // ---------------- config & defaults ----------------
  task set_defaults;
    begin
      gpio_status = 8'h40; rssi_half_db = 11'd150; tsf_runtime_val = 0;
      iq0 = 32'b0; iq1 = 32'b0; iq_strobe = 0;
      demod_is_ongoing = 0; ofdm_symbol_eq_out_pulse = 0;
      long_preamble_detected = 0; short_preamble_detected = 0; ht_unsupport = 0;
      pkt_rate = 8'b01011; pkt_len = 16'd0;
      csi = 0; csi_valid = 0; phase_offset_taken = 0; equalizer = 0; equalizer_valid = 0;
      pkt_header_valid = 0; pkt_header_valid_strobe = 0; phy_type = 2'b01;
      tx_control_state = 0;
      FC_DI = 0; FC_DI_valid = 0;
      addr1 = 48'h0a0b0c010203; addr2 = 48'h0a0b0c010204; addr3 = 0;
      addr1_valid = 0; addr2_valid = 0; addr3_valid = 0;
      fcs_in_strobe = 0; fcs_ok = 1;
      block_rx_dma_to_ps = 0; block_rx_dma_to_ps_valid = 0; ch_idle_final = 1;
      phy_tx_start = 0; tx_pkt_need_ack = 0; phy_tx_started = 0; phy_tx_done = 0;
      tx_bb_is_ongoing = 0; tx_rf_is_ongoing = 0; tx_pkt_iq_to_dac_ongoing = 0; retrans_in_progress = 0;
      slv_reg_wren_signal = 0; axi_awaddr_core = 0;
      iq_capture = 1; iq_capture_cfg = 2'b00; csi_iq_combined = 1;
      iq_trigger_select = 8; iq_trigger_free_run_flag = 0; iq_source_select = 2'b00;
      disable_tx_pkt_need_ack_check = 0; PPDU_FORMAT_target = 0;
      rssi_or_iq_th = 0; gain_th = 0;
      pre_trigger_len = PRE_TRIG_OK[11:0]; iq_len_target = IQ_LEN[11:0];
      tx_control_state_target = 0; phy_type_target = 0;
      FC_target = 16'h1111; addr1_target = 32'b0; addr2_target = 32'b0;
      match_cfg = 4'b0001; // bit0 = FC enforced; addr1/addr2 free
      num_eq = NUM_EQ;
      m_axis_start_mode = 2'b01; m_axis_start_ext_trigger = 0;
      data_to_pl = 0; S_AXIS_TVALID = 0; S_AXIS_TLAST = 0;
      s_axis_data_count = 0; emptyn_to_pl = 0;
      M_AXIS_TVALID = 0; M_AXIS_TLAST = 0;
      count_seed = 3800; seed_count = 0;
    end
  endtask

  // ---------------- m_axis FIFO count ----------------
  always @(posedge clk) begin
    if (!rstn) begin
      m_axis_fifo_count <= 0;
    end else if (clear_count) begin
      m_axis_fifo_count <= 0;
    end else if (seed_count) begin
      m_axis_fifo_count <= count_seed;
    end else if (data_to_ps_valid) begin
      m_axis_fifo_count <= m_axis_fifo_count + 1;
    end
  end
  assign m_axis_data_count = m_axis_fifo_count;
  assign fulln_to_pl = 1'b1;

  // ---------------- capture ----------------
  always @(posedge clk) begin
    if (data_to_ps_valid) begin
      out_mem[out_cnt] = data_to_ps;
      out_cnt = out_cnt + 1;
`ifdef DEBUG_MON
      $display("%0t out[%0d] = %016h", $time, out_cnt-1, data_to_ps);
`endif
    end
  end

  // ---------------- global sample stream ---------------
  always @(posedge clk) begin
    if (!rstn) begin
      sCnt <= 0; gsamp <= 0; iq_strobe <= 0;
      demod_is_ongoing <= 0;
      long_preamble_detected <= 0; csi_valid <= 0; pkt_header_valid_strobe <= 0;
      FC_DI_valid <= 0; addr1_valid <= 0; addr2_valid <= 0;
      ofdm_symbol_eq_out_pulse <= 0; fcs_in_strobe <= 0; seed_count <= 0;
      done_driving <= 0;
    end else if (done_driving) begin
      iq_strobe <= 0; demod_is_ongoing <= 0;
    end else begin
      // default: deassert single-cycle handshakes
      long_preamble_detected <= 0; pkt_header_valid_strobe <= 0;
      FC_DI_valid <= 0; addr1_valid <= 0; addr2_valid <= 0;
      ofdm_symbol_eq_out_pulse <= 0; fcs_in_strobe <= 0; seed_count <= 0; clear_count <= 0;

      if (sCnt == 4) begin
        sCnt <= 0; iq_strobe <= 1;
        region = gsamp / SPACING;
        rel    = gsamp % SPACING;
        iq0    <= {file_q[gsamp], file_i[gsamp]};
        tsf_runtime_val <= region*TSF_PKT_BASE + rel;

        // packet envelope: demod high through the region
        if (rel == 0)
          demod_is_ongoing <= 1;
        if (rel == SPACING-1)
          demod_is_ongoing <= 0;

        // handshakes at packet-relative offsets (values from per-region tables)
        if (rel == PRE_TRIG_OK) long_preamble_detected <= 1;
        if (rel == HDR_REL-30) pkt_len <= pkt_len_tab[region][15:0];
        if (rel == FC_REL) begin FC_DI_valid <= 1; FC_DI <= fc_tab[region][15:0]; end
        if (rel == A1_REL) addr1_valid <= 1;
        if (rel == A2_REL) addr2_valid <= 1;
        if (rel == HDR_REL) pkt_header_valid_strobe <= 1;
        if (rel >= EQ_START && ((rel-EQ_START) % 80) == 0 && rel < FCS_REL-100) ofdm_symbol_eq_out_pulse <= 1;
        if (rel == FCS_REL) begin fcs_in_strobe <= 1; fcs_ok <= 1; end
        if (rel == FC_REL-20) seed_count <= seed_tab[region];
        if (rel == 1) begin clear_count <= clear_tab[region]; pre_trigger_len <= trig_tab[region][11:0]; end

        if (gsamp == N_REGIONS*SPACING-1)
          done_driving <= 1;
        gsamp <= gsamp + 1;
      end else begin
        sCnt <= sCnt + 1; iq_strobe <= 0;
      end
    end
  end

  always @(posedge clk) begin
    pkt_header_valid <= 1; phase_offset_taken <= 32'h00001234;
  end

  // CSI estimator output: 64 BACK-TO-BACK valid clks (as openofdm_rx does) starting
  // right after the LTF. capture_src_flag flips to equalizer on the falling edge, so
  // single-spaced pulses would corrupt source selection -- keep them consecutive.
  integer csi_cnt;
  reg csi_run;
  always @(posedge clk) begin
    if (!rstn) begin csi_cnt <= 0; csi_run <= 0; csi_valid <= 0; csi <= 0; end
    else begin
      if (csi_run) begin
        csi_valid <= 1;
        csi <= 32'd1000 + csi_cnt;
        if (csi_cnt == 63) begin csi_run <= 0; csi_valid <= 0; end
        csi_cnt <= csi_cnt + 1;
      end else begin
        csi_valid <= 0;
        if ((sCnt == 4) && ((gsamp % SPACING) == (PRE_TRIG_OK+30))) begin
          csi_run <= 1; csi_cnt <= 0;
        end
      end
    end
  end

  // ---------------- scenario script ----------------
  initial begin
    errors = 0; out_cnt = 0; passed_records = 0; last_record_end = 0; file_len = 0; region = 0; rel = 0;

    // load test vector
    // xsim run dir = <proj>/<proj>.sim/sim_1/behav/xsim/, not tb dir -> relative $fopen fails.
    // Try relative first, then WSL absolute path as fallback.
    fd = $fopen("test_vec/data_in.txt", "r");
    if (fd == 0)
      fd = $fopen("/home/faweist/git/openwifi-hw/ip/side_ch/unit_test/side_ch_control_tb/test_vec/data_in.txt", "r");
    if (fd == 0) begin
      $display("FATAL: cannot open test_vec/data_in.txt");
      $fatal;
    end
    while (!$feof(fd) && file_len < N_REGIONS*SPACING) begin
      read_ret = $fscanf(fd, "%d %d", file_i[file_len], file_q[file_len]);
      if (read_ret == 2) file_len = file_len + 1;
    end
    $fclose(fd);
    if (file_len < N_REGIONS*SPACING) begin
      $display("FATAL: test vector has %0d samples, need %0d", file_len, N_REGIONS*SPACING);
      $fatal;
    end
    $display("loaded %0d test vector samples", file_len);

    set_defaults;
    // R0/R1 matched, R2/R3 FC-mismatched, R4 matched (re-locks R3 window),
    // R5 short 14B frame, R6 near-full drop, R7 recovery,
    // R8 pre_trigger_len wrong by 3 (C1 self-check, record must be shifted), R9 recovery
    pkt_len_tab[0]=40; pkt_len_tab[1]=40; pkt_len_tab[2]=40; pkt_len_tab[3]=40;
    pkt_len_tab[4]=40; pkt_len_tab[5]=14; pkt_len_tab[6]=40; pkt_len_tab[7]=40;
    pkt_len_tab[8]=40; pkt_len_tab[9]=40;
    fc_tab[0]=16'h1111; fc_tab[1]=16'h1111; fc_tab[2]=16'h9999; fc_tab[3]=16'h9999;
    fc_tab[4]=16'h1111; fc_tab[5]=16'h1111; fc_tab[6]=16'h1111; fc_tab[7]=16'h1111;
    fc_tab[8]=16'h1111; fc_tab[9]=16'h1111;
    trig_tab[0]=PRE_TRIG_OK; trig_tab[1]=PRE_TRIG_OK; trig_tab[2]=PRE_TRIG_OK; trig_tab[3]=PRE_TRIG_OK;
    trig_tab[4]=PRE_TRIG_OK; trig_tab[5]=PRE_TRIG_OK; trig_tab[6]=PRE_TRIG_OK; trig_tab[7]=PRE_TRIG_OK;
    trig_tab[8]=PRE_TRIG_OK-3; trig_tab[9]=PRE_TRIG_OK;   // wrong C1 for the self-check
    seed_tab[0]=0; seed_tab[1]=0; seed_tab[2]=0; seed_tab[3]=0;
    seed_tab[4]=0; seed_tab[5]=0; seed_tab[6]=1; seed_tab[7]=0;
    seed_tab[8]=0; seed_tab[9]=0;
    clear_tab[0]=0; clear_tab[1]=0; clear_tab[2]=0; clear_tab[3]=0;
    clear_tab[4]=0; clear_tab[5]=0; clear_tab[6]=0; clear_tab[7]=1;
    clear_tab[8]=0; clear_tab[9]=1;

    rstn = 0;
    repeat (20) @(posedge clk);
    rstn = 1;
    repeat (10) @(posedge clk);

    // run the whole stream through
    while (!done_driving) @(posedge clk);
    // settle for IQ readback + CSI push of the last record
    repeat (3000) @(posedge clk);
    verify_all;
    if (errors == 0)
      $display("PASS: side_ch_control_tb combined mode, %0d records verified", passed_records);
    else
      $display("FAIL: %0d assertion(s) failed (records verified %0d, pushed %0d)", errors, passed_records, out_cnt);
    $finish(errors == 0 ? 0 : 1);
  end

  // ---------------- verify ----------------
  integer k, rec, sample_idx, partial_ok;
  reg [15:0] exp_tsf_low;
  reg [63:0] w;
  integer csi_vals[0:63];

  task check;
    input integer cond;
    input [255*8-1:0] msg;
    begin
      if (!cond) begin
        errors = errors + 1;
        $display("FAIL @%0t: %s (out_cnt=%0d)", $time, msg, out_cnt);
      end
    end
  endtask

  task verify_one_record;
    input integer start;
    input integer pkt_id;
    input integer align_off;      // expected abs sample index of first IQ word (0 if unaligned test)
    begin
      // record layout: [0] IQ TSF, [1..IQ_LEN] samples, [IQ_LEN+1] CSI TSF,
      //                [IQ_LEN+2] phase, [IQ_LEN+3 .. IQ_LEN+2+56] CSI
      // 1) IQ TSF = pkt_id*TSF_PKT_BASE (locked at long_preamble_detected, tsf=pkt*base+300)
      check(out_mem[start][63:0] == ((pkt_id*TSF_PKT_BASE) + PRE_TRIG_OK), "PQ_t_q_tsf_match");
      // 2) IQ first sample = file_i[align_off]
      w = out_mem[start+1];
      check(w[15:0] == file_i[align_off], "P_first_sample_alignment");
      check(w[15:0] != file_i[align_off+3], "P_first_sample_not_shifted3");
      // 3) all 440 samples match file_i sequentially
      sample_idx = align_off;
      for (k = 0; k < IQ_LEN; k = k + 1) begin
        w = out_mem[start+1+k];
        if (w[15:0] != file_i[sample_idx+k]) begin
          errors = errors + 1;
          if (errors < 10)
            $display("FAIL: IQ sample %0d (abs %0d) mismatch exp %0d got %0d", k, sample_idx+k, file_i[sample_idx+k], w[15:0]);
        end
      end
      // 4) CSI TSF and phase
      check(out_mem[start+1+IQ_LEN][63:0] == (pkt_id*TSF_PKT_BASE) + HDR_REL, "P_csi_tsf_match");
      check(out_mem[start+2+IQ_LEN][31:0] == 32'h00001234, "P_phase_offset");
      // 5) CSI payload: 56 words. Upstream writes a trailing 0 marker at
      //    last_ofdm_symbol_flag which CSI_INFO may read last, so expect >=55
      //    in [1000,1063] and the rest being the 0 marker.
      partial_ok = 0;
      for (k = 0; k < 56; k = k + 1) begin
        w = out_mem[start+3+IQ_LEN+k];
        if (w[31:0] >= 1000 && w[31:0] <= 1063) partial_ok = partial_ok + 1;
      end
      check(partial_ok >= 55, "P_csi_values_in_range");
      if (partial_ok < 55) begin
        for (k = 0; k < 56; k = k + 1) begin
          w = out_mem[start+3+IQ_LEN+k];
          if (w[31:0] < 1000 || w[31:0] > 1063)
            $display("  csi[%0d]=%0d", k, w[31:0]);
        end
      end
      passed_records = passed_records + 1;
    end
  endtask

task verify_all;
    begin
      // expect records in regions 0,1,4,5,7,8,9 = 7 records
      check(out_cnt == 7*RECORD_LEN, "P_total_records_7x499");
      verify_one_record(0,            0, 0*SPACING);
      verify_one_record(RECORD_LEN,   1, 1*SPACING);
      // region 2/3 produced nothing: verify gap is contiguous records (byte count check above)
      verify_one_record(2*RECORD_LEN, 4, 4*SPACING);
      verify_one_record(3*RECORD_LEN, 5, 5*SPACING);
      verify_one_record(4*RECORD_LEN, 7, 7*SPACING);
      // region 8: pre_trigger_len wrong by 3 -> first IQ sample should sit at gstart+3
      verify_one_record(5*RECORD_LEN, 8, 8*SPACING + 3);
      // and prove the C1 self-check is effective: it must differ from the aligned position
      check(out_mem[5*RECORD_LEN+1][15:0] != file_i[8*SPACING], "P_wrong_trigger_caught");
      verify_one_record(6*RECORD_LEN, 9, 9*SPACING);
      // verify dumped record count equals expected
      check(passed_records == 7, "P_record_count_7");
    end
  endtask

endmodule