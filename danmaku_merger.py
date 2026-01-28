#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
B站弹幕合并工具
将多个录播姬生成的XML弹幕文件按时间顺序合并

移植自 BililiveRecorder.WPF/Pages/ToolboxDanmakuMergerPage
"""

import argparse
import glob
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple, Optional
import heapq


# 弹幕元素类型
DANMAKU_ELEMENTS = {'d', 'gift', 'sc', 'guard'}


def get_start_time(xml_path: str) -> Optional[datetime]:
    """
    从XML文件中读取录制开始时间
    对应 DanmakuStartTimeHandler
    """
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        
        # 查找 BililiveRecorderRecordInfo 元素
        for child in root:
            if child.tag == 'BililiveRecorderRecordInfo':
                start_time_str = child.get('start_time')
                if start_time_str:
                    # 解析 ISO 8601 格式时间
                    try:
                        return datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
                    except ValueError:
                        # 尝试其他格式
                        return datetime.strptime(start_time_str, '%Y-%m-%dT%H:%M:%S%z')
        return None
    except Exception as e:
        print(f"警告: 无法读取文件 {xml_path} 的开始时间: {e}")
        return None


def calculate_offsets(file_paths: List[str]) -> Tuple[List[datetime], List[int]]:
    """
    计算各文件相对于最早文件的时间偏移（秒）
    对应 ToolboxDanmakuMergerPage.CalculateOffsets
    """
    start_times = []
    for path in file_paths:
        st = get_start_time(path)
        if st is None:
            # 如果无法获取时间，使用当前时间作为默认值
            st = datetime.now(timezone.utc)
        start_times.append(st)
    
    # 找到最早的时间
    min_time = min(start_times)
    
    # 计算偏移量（秒）
    offsets = []
    for st in start_times:
        delta = st - min_time
        offsets.append(int(delta.total_seconds()))
    
    return start_times, offsets


def parse_danmaku_time(element: ET.Element) -> float:
    """解析弹幕元素的时间（秒）"""
    tag = element.tag
    
    if tag == 'd':
        # 弹幕时间存储在 p 属性的第一个逗号前
        p_attr = element.get('p', '')
        if p_attr:
            parts = p_attr.split(',')
            if parts:
                try:
                    return float(parts[0])
                except ValueError:
                    pass
    elif tag in ('gift', 'sc', 'guard'):
        # 礼物/SC/舰长时间存储在 ts 属性
        ts = element.get('ts')
        if ts:
            try:
                return float(ts)
            except ValueError:
                pass
    
    return 0.0


def update_timestamp(element: ET.Element, offset_seconds: int) -> float:
    """
    更新弹幕元素的时间戳
    对应 DanmakuMergerHandler.UpdateTimestamp
    """
    tag = element.tag
    offset = offset_seconds
    
    if tag == 'd':
        p_attr = element.get('p', '')
        if p_attr:
            parts = p_attr.split(',')
            if parts:
                try:
                    original_time = float(parts[0])
                    new_time = original_time + offset
                    parts[0] = f"{new_time:.3f}"
                    element.set('p', ','.join(parts))
                    return new_time
                except ValueError:
                    pass
    elif tag in ('gift', 'sc', 'guard'):
        ts = element.get('ts')
        if ts:
            try:
                original_time = float(ts)
                new_time = original_time + offset
                element.set('ts', f"{new_time:.3f}")
                return new_time
            except ValueError:
                pass
    
    return 0.0


def read_record_info(root: ET.Element) -> Optional[ET.Element]:
    """读取 BililiveRecorderRecordInfo 元素"""
    for child in root:
        if child.tag == 'BililiveRecorderRecordInfo':
            return child
    return None


def merge_danmaku_files(input_paths: List[str], output_path: str, offsets: Optional[List[int]] = None):
    """
    合并多个弹幕文件
    对应 DanmakuMergerHandler.Handle
    """
    if len(input_paths) < 2:
        print("错误: 至少需要2个输入文件")
        return False
    
    # 如果没有提供偏移量，自动计算
    if offsets is None:
        _, offsets = calculate_offsets(input_paths)
    
    if len(offsets) != len(input_paths):
        print("错误: 偏移量数量必须与输入文件数量一致")
        return False
    
    print(f"开始合并 {len(input_paths)} 个文件...")
    for i, path in enumerate(input_paths):
        print(f"  [{i}] {path} (偏移: {offsets[i]}秒)")
    
    # 解析所有文件
    file_data = []
    base_record_info = None
    
    for i, path in enumerate(input_paths):
        try:
            tree = ET.parse(path)
            root = tree.getroot()
            
            # 获取录制信息（使用偏移量最小的文件）
            record_info = read_record_info(root)
            if record_info is not None:
                if base_record_info is None or offsets[i] == min(offsets):
                    base_record_info = record_info
            
            # 收集所有弹幕元素
            danmaku_list = []
            for child in root:
                if child.tag in DANMAKU_ELEMENTS:
                    # 复制元素以避免修改原始数据
                    el_copy = ET.Element(child.tag, child.attrib)
                    el_copy.text = child.text
                    # 更新时间戳
                    new_time = update_timestamp(el_copy, offsets[i])
                    danmaku_list.append((new_time, el_copy, i))
            
            file_data.append(danmaku_list)
            print(f"  文件 {i}: 读取了 {len(danmaku_list)} 条弹幕")
        except Exception as e:
            print(f"错误: 无法解析文件 {path}: {e}")
            return False
    
    # 使用堆合并所有弹幕（按时间排序）
    # 每个文件创建一个迭代器
    iterators = [iter(lst) for lst in file_data]
    heap = []
    
    # 初始化堆：每个文件的第一条弹幕
    for file_idx, it in enumerate(iterators):
        try:
            item = next(it)
            # 堆元素: (时间, 元素, 文件索引, 迭代器)
            heapq.heappush(heap, (item[0], item[1], file_idx, it))
        except StopIteration:
            pass
    
    # 创建输出XML
    output_root = ET.Element('i')
    
    # 添加注释
    comment = ET.Comment("""
B站录播姬弹幕合并工具生成
本文件的弹幕信息兼容B站主站视频弹幕XML格式
本XML自带样式可以在浏览器里打开（推荐使用Chrome）

sc 为SuperChat
gift为礼物
guard为上船

attribute "raw" 为原始数据
""")
    output_root.append(comment)
    
    # 添加标准元素
    ET.SubElement(output_root, 'chatserver').text = 'chat.bilibili.com'
    ET.SubElement(output_root, 'chatid').text = '0'
    ET.SubElement(output_root, 'mission').text = '0'
    ET.SubElement(output_root, 'maxlimit').text = '1000'
    ET.SubElement(output_root, 'state').text = '0'
    ET.SubElement(output_root, 'real_name').text = '0'
    ET.SubElement(output_root, 'source').text = '0'
    
    # 添加录播姬信息
    bililive_elem = ET.SubElement(output_root, 'BililiveRecorder')
    bililive_elem.set('version', 'python-merger')
    bililive_elem.set('merged', 'by python script')
    
    # 添加录制信息
    if base_record_info is not None:
        output_root.append(base_record_info)
    
    # 添加XSL样式（用于浏览器显示）
    style_content = """<z:stylesheet version="1.0" id="s" xml:id="s" xmlns:z="http://www.w3.org/1999/XSL/Transform"><z:output method="html"/><z:template match="/"><html><meta name="viewport" content="width=device-width"/><title>B站录播姬弹幕文件 - <z:value-of select="/i/BililiveRecorderRecordInfo/@name"/></title><style>body{margin:0}h1,h2,p,table{margin-left:5px}table{border-spacing:0}td,th{border:1px solid grey;padding:1px}th{position:sticky;top:0;background:#4098de}tr:hover{background:#d9f4ff}div{overflow:auto;max-height:80vh;max-width:100vw;width:fit-content}</style><h1><a href="https://rec.danmuji.org">B站录播姬</a>弹幕XML文件</h1><p>本文件不支持在 IE 浏览器里预览，请使用 Chrome Firefox Edge 等浏览器。</p><p>文件用法参考文档 <a href="https://rec.danmuji.org/user/danmaku/">https://rec.danmuji.org/user/danmaku/</a></p><table><tr><td>录播姬版本</td><td><z:value-of select="/i/BililiveRecorder/@version"/></td></tr><tr><td>房间号</td><td><z:value-of select="/i/BililiveRecorderRecordInfo/@roomid"/></td></tr><tr><td>主播名</td><td><z:value-of select="/i/BililiveRecorderRecordInfo/@name"/></td></tr><tr><td>录制开始时间</td><td><z:value-of select="/i/BililiveRecorderRecordInfo/@start_time"/></td></tr><tr><td><a href="#d">弹幕</a></td><td>共<z:value-of select="count(/i/d)"/>条记录</td></tr><tr><td><a href="#guard">上船</a></td><td>共<z:value-of select="count(/i/guard)"/>条记录</td></tr><tr><td><a href="#sc">SC</a></td><td>共<z:value-of select="count(/i/sc)"/>条记录</td></tr><tr><td><a href="#gift">礼物</a></td><td>共<z:value-of select="count(/i/gift)"/>条记录</td></tr></table><h2 id="d">弹幕</h2><div id="dm"><table><tr><th>用户名</th><th>出现时间</th><th>用户ID</th><th>弹幕</th><th>参数</th></tr><z:for-each select="/i/d"><tr><td><z:value-of select="@user"/></td><td></td><td></td><td><z:value-of select="."/></td><td><z:value-of select="@p"/></td></tr></z:for-each></table></div><script>Array.from(document.querySelectorAll('#dm tr')).slice(1).map(t=>t.querySelectorAll('td')).forEach(t=>{let p=t[4].textContent.split(','),a=p[0];t[1].textContent=`${(Math.floor(a/60/60)+'').padStart(2,0)}:${(Math.floor(a/60%60)+'').padStart(2,0)}:${(a%60).toFixed(3).padStart(6,0)}`;t[2].innerHTML=`<a target=_blank rel="nofollow noreferrer" href="https://space.bilibili.com/${p[6]}">${p[6]}</a>`})</script><h2 id="guard">舰长购买</h2><div><table><tr><th>用户名</th><th>用户ID</th><th>舰长等级</th><th>购买数量</th><th>出现时间</th></tr><z:for-each select="/i/guard"><tr><td><z:value-of select="@user"/></td><td><a rel="nofollow noreferrer"><z:attribute name="href"><z:text>https://space.bilibili.com/</z:text><z:value-of select="@uid" /></z:attribute><z:value-of select="@uid"/></a></td><td><z:value-of select="@level"/></td><td><z:value-of select="@count"/></td><td><z:value-of select="@ts"/></td></tr></z:for-each></table></div><h2 id="sc">SuperChat 醒目留言</h2><div><table><tr><th>用户名</th><th>用户ID</th><th>内容</th><th>显示时长</th><th>价格</th><th>出现时间</th></tr><z:for-each select="/i/sc"><tr><td><z:value-of select="@user"/></td><td><a rel="nofollow noreferrer"><z:attribute name="href"><z:text>https://space.bilibili.com/</z:text><z:value-of select="@uid" /></z:attribute><z:value-of select="@uid"/></a></td><td><z:value-of select="."/></td><td><z:value-of select="@time"/></td><td><z:value-of select="@price"/></td><td><z:value-of select="@ts"/></td></tr></z:for-each></table></div><h2 id="gift">礼物</h2><div><table><tr><th>用户名</th><th>用户ID</th><th>礼物名</th><th>礼物数量</th><th>出现时间</th></tr><z:for-each select="/i/gift"><tr><td><z:value-of select="@user"/></td><td><a rel="nofollow noreferrer"><z:attribute name="href"><z:text>https://space.bilibili.com/</z:text><z:value-of select="@uid" /></z:attribute><z:value-of select="@uid"/></a></td><td><z:value-of select="@giftname"/></td><td><z:value-of select="@giftcount"/></td><td><z:value-of select="@ts"/></td></tr></z:for-each></table></div></html></z:template></z:stylesheet>"""
    
    style_elem = ET.SubElement(output_root, 'BililiveRecorderXmlStyle')
    # 使用字符串方式添加样式内容
    style_elem.text = style_content
    
    # 合并弹幕
    merged_count = 0
    while heap:
        time_val, element, file_idx, iterator = heapq.heappop(heap)
        output_root.append(element)
        merged_count += 1
        
        # 从同一文件读取下一条
        try:
            next_item = next(iterator)
            heapq.heappush(heap, (next_item[0], next_item[1], file_idx, iterator))
        except StopIteration:
            pass
        
        # 显示进度
        if merged_count % 1000 == 0:
            print(f"  已合并 {merged_count} 条弹幕...")
    
    print(f"  总共合并了 {merged_count} 条弹幕")
    
    # 写入输出文件
    try:
        # 注册命名空间
        ET.register_namespace('', '')
        
        tree = ET.ElementTree(output_root)
        
        # 写入文件，保持格式，每条弹幕一行
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            f.write('<?xml-stylesheet type="text/xsl" href="#s"?>\n')
            
            # 手动格式化输出，每条弹幕占一行
            def write_element(elem, indent=0):
                indent_str = '  ' * indent
                
                # 处理Comment元素
                if isinstance(elem, ET.Comment):
                    f.write(f'{indent_str}<!--{elem.text}-->\n')
                    return
                
                if elem.tag == 'BililiveRecorderXmlStyle':
                    # 样式元素特殊处理，写入原始XML
                    f.write(f'{indent_str}<BililiveRecorderXmlStyle>')
                    if elem.text:
                        f.write(elem.text)
                    f.write('</BililiveRecorderXmlStyle>\n')
                elif elem.tag in DANMAKU_ELEMENTS:
                    # 弹幕元素单行输出
                    f.write(f'{indent_str}<{elem.tag}')
                    for key, value in elem.attrib.items():
                        f.write(f' {key}="{value}"')
                    if elem.text:
                        f.write(f'>{elem.text}</{elem.tag}>\n')
                    else:
                        f.write(f'/>\n')
                else:
                    # 其他元素
                    f.write(f'{indent_str}<{elem.tag}')
                    for key, value in elem.attrib.items():
                        f.write(f' {key}="{value}"')
                    
                    children = list(elem)
                    text = elem.text.strip() if elem.text and elem.text.strip() else ''
                    
                    if not children and not text:
                        f.write('/>\n')
                    elif not children and text:
                        f.write(f'>{text}</{elem.tag}>\n')
                    else:
                        f.write('>\n')
                        if text:
                            f.write(f'{indent_str}  {text}\n')
                        for child in children:
                            write_element(child, indent + 1)
                        f.write(f'{indent_str}</{elem.tag}>\n')
            
            f.write('<i>\n')
            for child in output_root:
                write_element(child, 1)
            f.write('</i>\n')
        
        print(f"\n成功! 合并后的文件已保存到: {output_path}")
        return True
    except Exception as e:
        print(f"错误: 无法写入输出文件: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description='B站录播姬弹幕合并工具 - 将多个XML弹幕文件按时间顺序合并',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s file1.xml file2.xml -o merged.xml
  %(prog)s *.xml -o output.xml
  %(prog)s a.xml b.xml c.xml -o result.xml --offsets 0 60 120
        """
    )
    
    parser.add_argument(
        'input_files',
        nargs='+',
        help='输入的XML弹幕文件路径（至少2个）'
    )
    
    parser.add_argument(
        '-o', '--output',
        required=True,
        help='输出文件路径'
    )
    
    parser.add_argument(
        '--offsets',
        nargs='+',
        type=int,
        help='手动指定每个文件的时间偏移量（秒），如果不指定则自动计算'
    )
    
    parser.add_argument(
        '--auto-offsets',
        action='store_true',
        default=True,
        help='自动根据文件录制时间计算偏移量（默认开启）'
    )
    
    args = parser.parse_args()
    
    # 展开通配符路径
    expanded_files = []
    for pattern in args.input_files:
        if '*' in pattern or '?' in pattern:
            expanded_files.extend(glob.glob(pattern))
        else:
            expanded_files.append(pattern)
    
    # 验证输入文件
    valid_files = []
    for f in expanded_files:
        path = Path(f)
        if not path.exists():
            print(f"警告: 文件不存在: {f}")
        elif not f.endswith('.xml'):
            print(f"警告: 跳过非XML文件: {f}")
        else:
            valid_files.append(f)
    
    if len(valid_files) < 2:
        print("错误: 至少需要2个有效的XML文件")
        sys.exit(1)
    
    # 执行合并
    success = merge_danmaku_files(
        valid_files,
        args.output,
        args.offsets
    )
    
    if not success:
        sys.exit(1)


if __name__ == '__main__':
    main()
