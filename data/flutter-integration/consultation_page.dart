import 'dart:async';
import 'dart:convert';
import 'dart:math';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:web_socket_channel/web_socket_channel.dart';
import 'consultation_platform_stub.dart'
    if (dart.library.js_interop) 'consultation_platform_web.dart';

const _yellow = Color(0xFFF1C744);
const _background = Color(0xFFFFFBE8);
const _labels = {
  'symptoms': '호소한 증상', 'discussion': '상담 내용',
  'medication_guidance': '복약 안내', 'precautions': '주의사항',
  'follow_up': '후속 안내', 'needs_verification': '확인이 필요한 내용',
};

class ConsultationPage extends StatefulWidget {
  const ConsultationPage({super.key});
  @override
  State<ConsultationPage> createState() => _ConsultationPageState();
}

class _ConsultationPageState extends State<ConsultationPage> {
  final _client = http.Client();
  final _input = TextEditingController();
  final _scroll = ScrollController();
  final _fields = {for (final key in _labels.keys) key: TextEditingController()};
  final _messages = <int, Map<String, dynamic>>{};
  final _role = Uri.base.queryParameters['role'] == 'pharmacist' ? 'pharmacist' : 'user';
  Map<String, dynamic>? _session;
  Map<String, dynamic> _room = {}, _summary = {};
  WebSocketChannel? _socket;
  StreamSubscription<dynamic>? _subscription;
  Timer? _poll, _reconnect;
  bool _busy = false, _sending = false, _refreshing = false, _chat = true, _connected = false;
  bool _draftLoaded = false;
  String? _error, _video;
  String? _pendingId, _pendingText;
  bool get _ended => _room['ended_at'] != null;
  bool get _pharmacist => _role == 'pharmacist';
  String get _path => '/consultations/${_session!['id']}';
  bool get _local => kIsWeb && Uri.base.port == 8001 && {'localhost','127.0.0.1'}.contains(Uri.base.host);

  @override
  void initState() {
    super.initState();
    if (_local) {
      final saved = loadSession();
      if (saved != null) {
        try { _session = jsonDecode(saved) as Map<String, dynamic>; }
        catch (_) { _session = null; }
      }
      if (_session != null) { _video = _session!['room_url'] as String?; _connect(); _refresh(); }
      _poll = Timer.periodic(const Duration(seconds: 2), (_) => _refresh());
    }
  }

  Future<dynamic> _request(String path, {String method = 'GET', Object? body, bool demo = false}) async {
    final headers = <String, String>{
      if (_session != null) 'Authorization': 'Bearer ${_session!['${_role}_token']}',
      if (demo) 'X-Moyak-Demo': '1',
      if (body != null) 'Content-Type': 'application/json',
    };
    final uri = Uri.base.resolve(path);
    final response = await (method == 'GET' ? _client.get(uri, headers: headers)
        : _client.post(uri, headers: headers, body: body == null ? null : jsonEncode(body)))
        .timeout(const Duration(seconds: 35));
    final data = jsonDecode(utf8.decode(response.bodyBytes));
    if (response.statusCode >= 400) {
      throw Exception(data is Map && data['detail'] is String ? data['detail'] : '요청에 실패했습니다 (${response.statusCode}).');
    }
    return data;
  }

  Future<void> _run(Future<void> Function() action) async {
    if (_busy) return;
    setState(() { _busy = true; _error = null; });
    try { await action(); }
    catch (e) { if (mounted) setState(() => _error = '$e'); }
    finally { if (mounted) setState(() => _busy = false); }
  }

  Future<void> _start() async {
    _session = Map<String, dynamic>.from(await _request('/demo/start', method: 'POST', demo: true));
    if (!mounted) return;
    _room = {}; _summary = {}; _messages.clear(); _video = null; _draftLoaded = false;
    saveSession(jsonEncode(_session));
    _connect();
    await _refresh();
    await _joinVideo();
  }

  Future<void> _joinVideo() async {
    final data = await _request('/demo/${_session!['id']}/video', method: 'POST');
    final url = Uri.parse(data['room_url'] as String);
    if (url.scheme != 'https' || !url.host.endsWith('.daily.co')) throw Exception('영상방 주소가 올바르지 않습니다.');
    if (!mounted) return;
    setState(() => _video = url.toString());
    _session!['room_url'] = _video;
    saveSession(jsonEncode(_session));
  }

  void _merge(List<dynamic> items) {
    if (!mounted) return;
    setState(() { for (final item in items) { _messages[item['id'] as int] = Map<String,dynamic>.from(item); } });
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted && _scroll.hasClients) _scroll.jumpTo(_scroll.position.maxScrollExtent);
    });
  }

  Future<void> _connect() async {
    _reconnect?.cancel();
    await _subscription?.cancel();
    _socket?.sink.close();
    if (!mounted || _session == null) return;
    final channel = WebSocketChannel.connect(Uri.base.resolve('$_path/messages/ws').replace(scheme: Uri.base.scheme == 'https' ? 'wss' : 'ws'));
    _socket = channel;
    void disconnected() {
      if (!mounted || _socket != channel) return;
      setState(() => _connected = false);
      if (channel.closeCode == 1008) { setState(() => _error = '인증이 만료되었습니다. 상담 화면을 다시 시작해주세요.'); return; }
      _reconnect?.cancel();
      _reconnect = Timer(const Duration(seconds: 2), _connect);
    }
    _subscription = channel.stream.listen((event) {
      final data = jsonDecode(event as String);
      if (data['type'] == 'messages') _merge(data['items']);
    }, onError: (_) => disconnected(), onDone: disconnected);
    try {
      await channel.ready;
      if (!mounted || _socket != channel) return;
      channel.sink.add(jsonEncode({'token': _session!['${_role}_token'], 'after': _messages.isEmpty ? 0 : _messages.keys.reduce(max)}));
      setState(() => _connected = true);
    } catch (_) { disconnected(); }
  }

  Future<void> _refresh() async {
    if (_session == null || _refreshing || !mounted) return;
    _refreshing = true;
    try {
      final results = await Future.wait([_request('$_path/session'), _request('$_path/summary')]);
      if (!mounted) return;
      setState(() {
        _room = Map<String,dynamic>.from(results[0]);
        _summary = Map<String,dynamic>.from(results[1]);
        if (_ended) _video = null;
        if (_summary['content'] != null && !_draftLoaded) {
          for (final key in _labels.keys) { _fields[key]!.text = (_summary['content'][key] as List).join('\n'); }
          _draftLoaded = true;
        }
      });
    } catch (e) { if (mounted) setState(() => _error = '$e'); }
    finally { _refreshing = false; }
  }

  Future<void> _send() async {
    final text = _input.text.trim();
    if (text.isEmpty || _sending || _ended) return;
    if (_pendingText != text) {
      _pendingText = text;
      _pendingId = '${DateTime.now().microsecondsSinceEpoch}-${Random.secure().nextInt(1<<32)}';
    }
    setState(() { _sending = true; _error = null; });
    try {
      final message = await _request('$_path/messages', method:'POST', body:{'client_id':_pendingId,'text':text});
      if (!mounted) return;
      _merge([message]);
      if (_input.text.trim() == text) _input.clear();
      _pendingId = null; _pendingText = null;
    } catch (e) { if (mounted) setState(() => _error = '$e'); }
    finally { if (mounted) setState(() => _sending = false); }
  }

  Future<void> _finish() async {
    final existing = await _request(_path);
    if (existing['status'] == 'pending') {
      await _request('$_path/decision', method:'POST', body:{'pharmacist_id':_session!['pharmacist_id'],'approve':false,'reason':'시연 상담 종료 · 약품 구매 승인 없음'});
    }
    await _request('$_path/session/end', method:'POST');
    await _refresh();
  }

  @override
  void dispose() {
    _poll?.cancel(); _reconnect?.cancel(); _subscription?.cancel(); _socket?.sink.close();
    _client.close(); _input.dispose(); _scroll.dispose();
    for (final controller in _fields.values) { controller.dispose(); }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    backgroundColor: _background,
    appBar: AppBar(backgroundColor: _background, title: const Text('모약 · 영상상담'), actions:[
      IconButton(tooltip:_chat?'채팅 닫기':'채팅 열기', onPressed:()=>setState(()=>_chat=!_chat), icon:Icon(_chat?Icons.chat_bubble:Icons.chat_bubble_outline)),
    ]),
    body: !_local ? const Center(child:SelectableText('PC 브라우저에서 http://127.0.0.1:8001/app/ 로 접속해주세요.')) : Column(children:[
      Container(width:double.infinity, padding:const EdgeInsets.symmetric(horizontal:20,vertical:8), color:const Color(0xFFFFF0B4), child:Text('로컬 시연 · ${_pharmacist?'약사':'사용자'} 화면 · 실제 회원 로그인 및 Supabase 저장 미연결',style:const TextStyle(fontSize:12))),
      if (_error != null) Padding(padding:const EdgeInsets.all(8),child:Text(_error!,style:const TextStyle(color:Colors.red),maxLines:3)),
      Padding(padding:const EdgeInsets.all(12), child:Wrap(spacing:10,runSpacing:8,crossAxisAlignment:WrapCrossAlignment.center,children:[
        const CircleAvatar(backgroundColor:_yellow,child:Icon(Icons.medical_services_outlined,color:Colors.white)),
        Text(_ended?'상담 종료':_session==null?'약사와 상담을 시작하세요':'${_connected?'● 연결됨':'연결 중'} · 영상상담',style:const TextStyle(fontWeight:FontWeight.bold)),
        if (_session == null && !_pharmacist) FilledButton(onPressed:_busy?null:()=>_run(_start),child:const Text('상담 시작')),
        if (_session != null && !_ended && _video == null) OutlinedButton(onPressed:_busy?null:()=>_run(_joinVideo),child:const Text('영상 연결')),
        if (_session != null && !_pharmacist && !_ended) OutlinedButton(onPressed:(){saveSession(jsonEncode(_session));openPeer();},child:const Text('약사 시연 화면 열기')),
        if (_session != null && _pharmacist && !_ended) FilledButton(onPressed:_busy?null:()=>_run(_finish),child:const Text('상담 종료')),
        if (_busy) const SizedBox(width:18,height:18,child:CircularProgressIndicator(strokeWidth:2)),
      ])),
      Expanded(child:LayoutBuilder(builder:(context, constraints) {
        final video = ClipRRect(borderRadius:BorderRadius.circular(20),child:ColoredBox(color:const Color(0xFF252525),child:_video!=null
          ? DailyVideo(key:ValueKey(_video),url:_video!)
          : Center(child:Column(mainAxisSize:MainAxisSize.min,children:[const Icon(Icons.video_call_outlined,color:_yellow,size:60),const SizedBox(height:12),Text(_ended?'상담이 종료되었습니다':'카메라와 마이크를 허용하고\n약사와 대화를 시작하세요',textAlign:TextAlign.center,style:const TextStyle(color:Colors.white,height:1.6))]))));
        // Keep this layout and its video child in place when chat visibility changes.
        return Padding(padding:const EdgeInsets.fromLTRB(16,0,16,12),child:Flex(direction:constraints.maxWidth>760?Axis.horizontal:Axis.vertical,children:[
          Expanded(flex:3,child:video),
          if (_chat) const SizedBox(width:12,height:12),
          if (_chat) Expanded(flex:2,child:_chatPanel()),
        ]));
      })),
      if (_session != null && !_ended) CheckboxListTile(dense:true,controlAffinity:ListTileControlAffinity.leading,
        title:const Text('채팅·음성 기록의 AI 요약 처리에 동의합니다.',style:TextStyle(fontSize:13)),
        subtitle:const Text('현재 이 화면의 요약은 입력한 채팅을 사용합니다. 통화 음성 자동 녹음은 연결되지 않았습니다.',style:TextStyle(fontSize:11)),
        value:_room['${_role}_consent_at']!=null,
        onChanged:_busy||_room['${_role}_consent_at']!=null?null:(_)=>_run(() async {await _request('$_path/session/consent',method:'POST');await _refresh();})),
      if (_ended) Padding(padding:const EdgeInsets.only(bottom:12),child:FilledButton.icon(onPressed:()=>showModalBottomSheet<void>(context:context,isScrollControlled:true,builder:(_)=>_summaryPanel()),icon:const Icon(Icons.auto_awesome),label:Text('AI 상담 요약 · ${_summaryLabel()}'))),
    ]),
  );

  Widget _chatPanel() {
    final items = _messages.values.toList()..sort((a,b)=>(a['id'] as int).compareTo(b['id'] as int));
    return Container(decoration:BoxDecoration(color:Colors.white,borderRadius:BorderRadius.circular(20)),padding:const EdgeInsets.all(14),child:Column(crossAxisAlignment:CrossAxisAlignment.stretch,children:[
      const Text('상담 채팅',style:TextStyle(fontSize:18,fontWeight:FontWeight.bold)),
      const Divider(),
      Expanded(child:items.isEmpty?const Center(child:Text('궁금한 내용을 채팅으로 남겨주세요.',style:TextStyle(color:Colors.grey))):ListView.builder(controller:_scroll,itemCount:items.length,itemBuilder:(context,index){
        final item=items[index]; final mine=item['sender_role']==_role;
        return Align(alignment:mine?Alignment.centerRight:Alignment.centerLeft,child:Container(margin:const EdgeInsets.symmetric(vertical:5),padding:const EdgeInsets.all(12),constraints:const BoxConstraints(maxWidth:300),decoration:BoxDecoration(color:mine?const Color(0xFFFFF0B4):const Color(0xFFF4F4F4),borderRadius:BorderRadius.circular(14)),child:Column(crossAxisAlignment:CrossAxisAlignment.start,children:[Text(mine?'나':_pharmacist?'사용자':'약사',style:const TextStyle(fontSize:11,color:Colors.grey)),const SizedBox(height:4),Text(item['text'] as String)])));
      })),
      Row(children:[Expanded(child:TextField(controller:_input,enabled:_session!=null&&!_ended,maxLength:4000,minLines:1,maxLines:3,decoration:const InputDecoration(hintText:'메시지를 입력하세요',counterText:'',border:OutlineInputBorder()),onSubmitted:(_)=>_send())),const SizedBox(width:8),IconButton.filled(tooltip:'메시지 전송',onPressed:_session==null||_ended||_sending?null:_send,icon:const Icon(Icons.arrow_upward))]),
    ]));
  }

  String _summaryLabel() => const {'not_requested':'요약 없음','queued':'생성 대기','processing':'생성 중','pending_review':'약사 확인 대기','published':'확인 완료','failed':'생성 실패'}[_summary['status']]??'확인 중';

  Widget _summaryPanel() => StatefulBuilder(builder:(context, update) => SafeArea(child:SizedBox(height:MediaQuery.sizeOf(context).height*.8,child:SingleChildScrollView(padding:const EdgeInsets.all(24),child:Column(crossAxisAlignment:CrossAxisAlignment.stretch,children:[
    Text('AI 상담 요약 · ${_summaryLabel()}',style:const TextStyle(fontSize:22,fontWeight:FontWeight.bold)),
    const SizedBox(height:12),
    if (_summary['content']==null) Text(_pharmacist?'요약을 생성하고 있습니다. 양측 동의 없이 종료한 상담은 요약되지 않습니다.':'약사가 확인한 요약이 이곳에 표시됩니다.'),
    if (_summary['content']!=null) for(final key in _labels.keys) Padding(padding:const EdgeInsets.symmetric(vertical:8),child:_pharmacist&&_summary['status']=='pending_review'
      ? TextField(controller:_fields[key],minLines:1,maxLines:6,decoration:InputDecoration(labelText:_labels[key],border:const OutlineInputBorder()))
      : Column(crossAxisAlignment:CrossAxisAlignment.start,children:[Text(_labels[key]!,style:const TextStyle(fontWeight:FontWeight.bold)),Text((_summary['content'][key] as List).isEmpty?'기록 없음':(_summary['content'][key] as List).join('\n'))])),
    if (_pharmacist&&_summary['status']=='pending_review') FilledButton(onPressed:_busy?null:()async {await _run(()async {await _request('$_path/summary/publish',method:'POST',body:{for(final key in _labels.keys)key:_fields[key]!.text.split('\n').map((s)=>s.trim()).where((s)=>s.isNotEmpty).toList()});await _refresh();});if(context.mounted)update((){});},child:const Text('약사 확인 완료 · 사용자에게 공개')),
    if (_pharmacist&&_summary['status']=='failed') OutlinedButton(onPressed:_busy?null:()async{await _run(()async{await _request('$_path/summary/retry',method:'POST');await _refresh();});if(context.mounted)update((){});},child:const Text('요약 다시 생성')),
    TextButton(onPressed:()async{await _refresh();if(context.mounted)update((){});},child:const Text('새로고침')),
    if (_error!=null) Text(_error!,style:const TextStyle(color:Colors.red)),
  ])))));
}
