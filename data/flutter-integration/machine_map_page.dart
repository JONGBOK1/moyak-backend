import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:latlong2/latlong.dart';
import 'package:http/http.dart' as http;
import 'package:url_launcher/url_launcher.dart';

class MachineMapPage extends StatefulWidget {
  const MachineMapPage({super.key});
  @override
  State<MachineMapPage> createState() => _MachineMapPageState();
}

class _MachineMapPageState extends State<MachineMapPage> {
  final _map = MapController();
  final _client = http.Client();
  final _search = TextEditingController();
  List<Map<String,dynamic>> _items = [];
  Map<String,dynamic>? _selected;
  String? _error;
  String _source = '';
  bool _loading = true, _ready = false, _limited = false;
  LatLng _point(Map<String,dynamic> m) => LatLng((m['latitude'] as num).toDouble(),(m['longitude'] as num).toDouble());
  List<Map<String,dynamic>> get _filtered {
    final query = _search.text.trim().toLowerCase();
    return _items.where((m)=>'${m['name']} ${m['address']}'.toLowerCase().contains(query)).toList();
  }

  @override
  void initState() { super.initState(); _load(); }
  Future<void> _load() async {
    setState(() { _loading = true; _error = null; });
    try {
      final r = await _client.get(Uri.base.resolve('/api/v1/map/machines')).timeout(const Duration(seconds:20));
      final data = jsonDecode(utf8.decode(r.bodyBytes));
      if (r.statusCode != 200) throw Exception(data['detail'] ?? '자판기 조회 실패');
      if (!mounted) return;
      setState(() {
        _items = (data['items'] as List).map((m)=>Map<String,dynamic>.from(m)).where((m)=>
          m['latitude'] is num && m['longitude'] is num &&
          (m['latitude'] as num).abs()<=90 && (m['longitude'] as num).abs()<=180).toList();
        _source = data['source'] == 'supabase' ? '등록 자판기' : '로컬 시연 데이터';
        _limited = data['truncated'] == true;
        _selected = _filtered.isEmpty ? null : _filtered.first;
      });
      if (_ready && _selected != null) _map.move(_point(_selected!),16);
    } catch (e) { if(mounted)setState(()=>_error='자판기 위치를 불러오지 못했습니다. 잠시 후 다시 시도해주세요.'); }
    finally { if(mounted)setState(()=>_loading=false); }
  }
  void _select(Map<String,dynamic> item) {
    setState(()=>_selected=item);
    if(_ready)_map.move(_point(item),16);
  }
  @override
  void dispose() { _client.close(); _map.dispose(); _search.dispose(); super.dispose(); }
  @override
  Widget build(BuildContext context) => SafeArea(child:ColoredBox(color:const Color(0xFFFFFBE8),child:Column(children:[
    Padding(padding:const EdgeInsets.fromLTRB(16,16,16,8),child:Row(children:[
      Expanded(child:TextField(controller:_search,onChanged:(_)=>setState((){if(!_filtered.any((m)=>m['id']==_selected?['id']))_selected=null;}),onSubmitted:(_){if(_filtered.isNotEmpty)_select(_filtered.first);},decoration:InputDecoration(prefixIcon:const Icon(Icons.search),hintText:'자판기 이름 또는 주소 검색',filled:true,fillColor:Colors.white,border:OutlineInputBorder(borderRadius:BorderRadius.circular(16),borderSide:BorderSide.none)))),
      IconButton(tooltip:'자판기 새로고침',onPressed:_loading?null:_load,icon:const Icon(Icons.refresh)),
    ])),
    if(_loading)const LinearProgressIndicator(color:Color(0xFFF1C744)),
    if(_error!=null)Padding(padding:const EdgeInsets.all(8),child:Text(_error!,style:const TextStyle(color:Colors.red))),
    if(!_loading&&_error==null)Padding(padding:const EdgeInsets.only(bottom:8),child:Text('$_source ${_filtered.length}곳${_limited?' · 최대 500곳 표시':''}',style:const TextStyle(fontSize:12,color:Colors.grey))),
    Expanded(child:FlutterMap(mapController:_map,options:MapOptions(
      initialCenter:const LatLng(37.4509,126.6539),initialZoom:15,minZoom:3,maxZoom:19,
      onMapReady:(){_ready=true;if(_selected!=null)_map.move(_point(_selected!),16);},
    ),children:[
      TileLayer(urlTemplate:'https://tile.openstreetmap.org/{z}/{x}/{y}.png',userAgentPackageName:'com.moyak.app',maxNativeZoom:19,panBuffer:0),
      MarkerLayer(markers:_filtered.map((m)=>Marker(point:_point(m),width:54,height:54,child:IconButton(tooltip:m['name'] as String,onPressed:()=>_select(m),icon:Icon(Icons.location_on,size:42,color:m['id']==_selected?['id']?const Color(0xFFE0AD14):const Color(0xFF3184BE))))).toList()),
      RichAttributionWidget(attributions:[TextSourceAttribution('OpenStreetMap contributors',onTap:()=>launchUrl(Uri.parse('https://www.openstreetmap.org/copyright')))]),
    ])),
    if(_filtered.isNotEmpty) SizedBox(height:48,child:ListView.separated(scrollDirection:Axis.horizontal,padding:const EdgeInsets.symmetric(horizontal:12),itemCount:_filtered.length,separatorBuilder:(_, index)=>const SizedBox(width:8),itemBuilder:(_,i)=>ChoiceChip(label:Text(_filtered[i]['name'] as String),selected:_selected?['id']==_filtered[i]['id'],onSelected:(_)=>_select(_filtered[i])))),
    if(_selected!=null)Container(width:double.infinity,padding:const EdgeInsets.fromLTRB(20,12,20,16),decoration:const BoxDecoration(color:Colors.white,borderRadius:BorderRadius.vertical(top:Radius.circular(24))),child:Column(crossAxisAlignment:CrossAxisAlignment.start,mainAxisSize:MainAxisSize.min,children:[
      Text(_selected!['name'] as String,style:const TextStyle(fontSize:19,fontWeight:FontWeight.bold)),
      Text(_selected!['address'] as String,style:const TextStyle(fontSize:12,color:Colors.grey)),
      if(_selected!['operating_hours']!=null)Text('운영 시간 · ${_selected!['operating_hours']}',style:const TextStyle(fontSize:12)),
      const SizedBox(height:8),
      Text((_selected!['item_count'] as num)==0?'등록된 재고 정보가 없습니다':'재고 ${_selected!['stock_count']}개 · ${_selected!['item_count']}개 품목',style:const TextStyle(color:Color(0xFF238B59),fontWeight:FontWeight.bold)),
      const Text('재고는 새로고침 시점의 정보입니다.',style:TextStyle(fontSize:11,color:Colors.grey)),
    ])) else if(!_loading&&_error==null) Padding(padding:const EdgeInsets.all(16),child:Text(_items.isEmpty?'위치가 등록된 운영 자판기가 없습니다.':'검색 결과를 선택하거나 다른 이름으로 검색해주세요.')),
  ])));
}
