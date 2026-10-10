# Vivado behavioral simulation script for side_ch_control combined mode (route A).
# Run on Linux x86_64 with Vivado:  vivado -mode batch -source side_ch_control_tb.tcl
# The tb instantiates side_ch_control directly (no AXI), so only the side_ch src set
# is needed plus this tb. side_ch_pre_def.v / has_side_ch_flag.v / fpga_scale.v are
# board-generated; provide a copy of the E310v2 content in ../pre_def/ (not committed):
#   side_ch_pre_def.v : `define SIDE_CH_LESS_BRAM 1
#   (side_ch_control.v only includes side_ch_pre_def.v; that's all we need here)
# Test vector: test_vec/data_in.txt (regenerate anytime with test_vec/gen_preamble.py).

set proj_name side_ch_control_tb_prj
set proj_dir  ./$proj_name
set part      xc7z020clg400-1
set src_dir   ../../src
set tb_dir    [pwd]
set pre_def   [pwd]/../pre_def
set vec_dir   [pwd]/test_vec

file delete -force $proj_dir

create_project $proj_name $proj_dir -part $part

# RTL under test (controlFsm + its two dependents)
add_files -norecurse [list \
  $src_dir/side_ch_control.v \
  $src_dir/dpram.v \
]
# testbench (SIProp case; do NOT add the verilator/ xpm model - Vivado has the real primitive)
add_files -norecurse $tb_dir/side_ch_control_tb.v

# include directory for `define files (empty content for control is sufficient)
set_property include_dirs $pre_def [get_filesets sim_1]

# test vector must be visible to $fopen at sim run time; set the sim working directory
set_property -name {xvlog.more_options} -value {--relax} -objects [get_filesets sim_1]
set_property top side_ch_control_tb [get_filesets sim_1]

# run behavioral simulation in batch
launch_simulation -mode behavioral
run all

# exit code: $finish in tb carries pass/fail (0 = pass) - retrieve from log
puts "SIMULATION DONE - inspect messages/log for PASS/FAIL"