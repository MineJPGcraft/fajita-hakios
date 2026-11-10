#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "../devices/fajita/loader/fajita_cmd_db.h"
static uint8_t table[512];
static void w16(unsigned off, uint16_t v) { table[off]=(uint8_t)v;table[off+1]=(uint8_t)(v>>8); }
static void w32(unsigned off, uint32_t v) { for(unsigned i=0;i<4;i++) table[off+i]=(uint8_t)(v>>(i*8)); }
static void init(void) {
    memset(table,0,sizeof(table));w32(0,1);w32(4,0x0c0330db);
    w16(8,4);w16(10,0);w16(12,64);w16(14,1);w16(16,0x100);
    memcpy(table+144,"ldoa28",6);w32(152,4);w32(160,0x40020);w16(164,2);
}
int main(void) {
    struct fajita_cmd_db_resource r,old;
    init();assert(fajita_cmd_db_ldoa28(table,sizeof(table),&r));
    assert(r.address==0x40020 && r.priority[0]==4 && r.version==0x100 && r.aux_length==2);
    old=r;table[4]=0;assert(!fajita_cmd_db_ldoa28(table,sizeof(table),&r));assert(!memcmp(&r,&old,sizeof(r)));
    init();w16(14,513);assert(!fajita_cmd_db_ldoa28(table,sizeof(table),&r));
    init();w16(10,0xfff0);assert(!fajita_cmd_db_ldoa28(table,sizeof(table),&r));
    init();w16(12,0xffff);assert(!fajita_cmd_db_ldoa28(table,sizeof(table),&r));
    init();w32(160,0x30020);assert(!fajita_cmd_db_ldoa28(table,sizeof(table),&r));
    init();w16(8,3);assert(!fajita_cmd_db_ldoa28(table,sizeof(table),&r));
    init();w32(160,0x40021);assert(!fajita_cmd_db_ldoa28(table,sizeof(table),&r));
    init();w16(14,2);memcpy(table+168,table+144,24);assert(!fajita_cmd_db_ldoa28(table,sizeof(table),&r));
    init();table[144]='x';assert(!fajita_cmd_db_ldoa28(table,sizeof(table),&r));
    assert(!fajita_cmd_db_ldoa28(NULL,sizeof(table),&r));assert(!fajita_cmd_db_ldoa28(table,143,&r));
    assert(!fajita_cmd_db_ldoa28(table,sizeof(table),NULL));
    assert(fajita_cmd_db_range(0x85fe0000,0x20000,0x85fc0000,0x8ab00000));
    assert(!fajita_cmd_db_range(0x85fe0001,0x20000,0x85fc0000,0x8ab00000));
    assert(!fajita_cmd_db_range(0x85fbf000,0x20000,0x85fc0000,0x8ab00000));
    assert(!fajita_cmd_db_range(0x8aaff000,0x20000,0x85fc0000,0x8ab00000));
    assert(!fajita_cmd_db_range(0x85fe0000,0x20001,0x85fc0000,0x8ab00000));
    assert(!fajita_cmd_db_range(0x85fe0000,0,0x85fc0000,0x8ab00000));
    puts("cmd-db: bounded dictionary, VRM resource, duplicates, alignment and atomic rejection passed");
}
