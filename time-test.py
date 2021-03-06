#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0
# Copyright 2019 NXP Semiconductors

from statistics import mean, stdev
import argparse
import sys
import re

NSEC_PER_SEC = 1000000000
# Maximum delay in nanoseconds for a PHY-to-PHY path
PATH_DELAY_THRESHOLD = 5000

def timespec_to_ns(ts):
    words = ts.split('.')

    if len(words) == 1:
        sec = 0
        nsec = int(words[0])
    elif len(words) == 2:
        sec = int(words[0])
        while len(words[1]) < 9:
            words[1] += '0'
        nsec = int(words[1])
    else:
        print('Invalid time {}'.format(ts))
        exit(1)

    return (sec * NSEC_PER_SEC) + nsec

def ns_to_timespec(ns, relative=False):
    prefix = ''

    ns = int(ns)
    if (ns < 0):
        prefix = '-'
        ns = -ns
    elif relative:
        prefix = '+'

    sec = int(ns / NSEC_PER_SEC)
    nsec = ns % NSEC_PER_SEC
    return '{}{}.{:09d}'.format(prefix, sec, nsec)

def utc_to_tai(ns):
    return ns + utc_offset

class tstamp_set():
    def __init__(self, seqid, gate, sw, hw):
        self.seqid = seqid
        self.gate = gate
        self.sw = sw
        self.hw = hw

def parse(raw_l2_send_txt, raw_l2_rcv_txt):

    r = results()

    # Sample line:
    # [1567611606.749999008] seqid 147 txtstamp 1567611643.749999195 swts 1567611606.750111880
    with open(raw_l2_send_txt, 'r') as raw_l2_send:
        tx_log = raw_l2_send.readlines()

    # Sample line:
    # [1567611606.725799008] src 00:04:9f:05:de:06 dst 00:04:9f:05:f4:ab ethertype 0x22f0 seqid 26 rxtstamp 1567611643.725804046 swts 1567611606.725822874
    with open(raw_l2_rcv_txt) as raw_l2_rcv:
        rx_log = raw_l2_rcv.readlines()

    for tx_line in tx_log:
        tx_words = tx_line.split()
        if (len(tx_words) < 7):
            # Skip malformed lines
            continue
        tx_seqid = int(tx_words[2])

        found = False

        for rx_line in rx_log:
            rx_words = rx_line.split()
            # Skip malformed lines
            if (len(rx_words) < 9):
                continue
            rx_seqid = int(rx_words[8])
            if (tx_seqid == rx_seqid):
                found = True
                break

        if not found:
            print("seqid {} lost".format(tx_seqid))
            continue

        rx_log.remove(rx_line)

        m = re.search('\[(.*)\]', tx_words[0])
        if not m:
            print("Malformed gate time {}".format(tx_words[0]))
            continue
        tx_gate_time = timespec_to_ns(m.group(1))
        tx_hwts = timespec_to_ns(tx_words[4])
        tx_swts = timespec_to_ns(tx_words[6])

        m = re.search('\[(.*)\]', rx_words[0])
        if not m:
            print("Malformed RX time {}".format(rx_words[0]))
            continue
        rx_gate_time = timespec_to_ns(m.group(1))
        rx_hwts = timespec_to_ns(rx_words[10])
        rx_swts = timespec_to_ns(rx_words[12])

        tx = tstamp_set(seqid=tx_seqid,
                        gate=utc_to_tai(tx_gate_time),
                        sw=utc_to_tai(tx_swts),
                        hw=tx_hwts)
        rx = tstamp_set(seqid=rx_seqid,
                        gate=utc_to_tai(rx_gate_time),
                        sw=utc_to_tai(rx_swts),
                        hw=rx_hwts)
        process(tx, rx, r)

    if (args.summary):
        print('All times are relative to the gate event (first column)')

    return r

def process(tx, rx, r):
    r.frame_count += 1

    path_delay = rx.hw - tx.hw
    if (abs(path_delay) > PATH_DELAY_THRESHOLD):
        r.path_deadline_misses += 1
    r.path_delay.append(path_delay)

    adjusted_gate = tx.gate + (r.cycles_missed * cycle_time)

    gate_delay = tx.hw - adjusted_gate
    if (abs(gate_delay) >= cycle_time):
        r.gate_deadline_misses += 1
        if (gate_delay > 0):
            r.cycles_missed += int(gate_delay / cycle_time)
    r.gate_delay.append(gate_delay)

    if (args.summary):
        return

    print('[{}] seqid {} HW TX {} SW TX {} HW RX {} SW RX {}'.format(
            ns_to_timespec(tx.gate),
            tx.seqid,
            ns_to_timespec(tx.hw - tx.gate, relative=True),
            ns_to_timespec(tx.sw - tx.gate, relative=True),
            ns_to_timespec(rx.hw - rx.gate, relative=True),
            ns_to_timespec(rx.sw - rx.gate, relative=True)))

def print_array(label, array):
    print('{} (ns): min {} max {} mean {} stddev {}'.format(label,
          ns_to_timespec(min(array)),
          ns_to_timespec(max(array)),
          ns_to_timespec(mean(array)),
          ns_to_timespec(stdev(array))))

def print_summary(r):
    print('Summary:')
    print_array('Gate delay', r.gate_delay)
    print_array('Path delay', r.path_delay)
    print('Gate deadline misses: {} ({}%)'.format(
          r.gate_deadline_misses,
          (r.gate_deadline_misses * 100) / r.frame_count))
    print('Path deadline misses: {} ({}%)'.format(
          r.path_deadline_misses,
          (r.path_deadline_misses * 100) / r.frame_count))
    print('Cycles missed: {}'.format(r.cycles_missed))

class results():
    def __init__(self, path_deadline_misses=0, gate_deadline_misses=0,
                 path_delay=[], gate_delay=[], frame_count=0,
                 cycles_missed=0):
        self.path_deadline_misses = path_deadline_misses
        self.gate_deadline_misses = gate_deadline_misses
        self.path_delay = path_delay
        self.gate_delay = gate_delay
        self.frame_count = frame_count
        self.cycles_missed = cycles_missed

parser = argparse.ArgumentParser(description='Process RT traffic timestamps.')
parser.add_argument('-t', '--tx-log', required=True,
                    help='Output from raw-l2-send')
parser.add_argument('-r', '--rx-log', required=True,
                    help='Output from raw-l2-rcv')
parser.add_argument('-u', '--utc-offset', required=True,
                    help='UTC-to-TAI offset (37 leap seconds as of 2019) in nanoseconds or sec.nsec format')
parser.add_argument('-c', '--cycle-time', required=True,
                    help='Cycle time, in nanoseconds or sec.nsec format')
parser.add_argument('-s', '--summary', action='store_true',
                    help='Don\'t print the frames, just the statistics')

args = parser.parse_args()

utc_offset = timespec_to_ns(args.utc_offset)
cycle_time = timespec_to_ns(args.cycle_time)
r = parse(args.tx_log, args.rx_log)
print_summary(r)
