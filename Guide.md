# VLM-GATE — Ayrıntılı Rehber

> Athena Agent senaryosunda LLM ↔ VMS ↔ VLM arasındaki köprü servisi.
> Bu dosya servisin **tamamını** sıfırdan anlatır: ne işe yarar, nasıl kurulur,
> her dosyada ne var, her fonksiyon ne yapıyor, hangi endpoint nasıl çağrılır,
> hangi senaryoda ne beklenir, hata çıkarsa nereye bakılır.

---

## İçindekiler

1. [Genel Bakış](#1-genel-bakış)
2. [Mimari ve Aktörler](#2-mimari-ve-aktörler)
3. [Akışın Hikâyesi](#3-akışın-hikâyesi)
4. [Kurulum](#4-kurulum)
5. [Konfigürasyon (.env)](#5-konfigürasyon-env)
6. [Dosyalar — Birebir Açıklama](#6-dosyalar--birebir-açıklama)
   - [config.py](#configpy)
   - [main.py](#mainpy)
   - [image_store.py](#image_storepy)
   - [fake_vms.py](#fake_vmspy)
   - [listen_sse.py](#listen_ssepy)
   - [trigger.py](#triggerpy)
7. [Endpoint Referansı](#7-endpoint-referansı)
8. [Şema Dönüşümleri](#8-şema-dönüşümleri)
9. [Image Akışı](#9-image-akışı)
10. [Çalıştırma Senaryoları](#10-çalıştırma-senaryoları)
11. [Uçtan Uca Test](#11-uçtan-uca-test)
12. [Sorun Giderme](#12-sorun-giderme)
13. [Ekibe Verilecek URL'ler](#13-ekibe-verilecek-urlller)
14. [Gelecek İyileştirmeler](#14-gelecek-iyileştirmeler)

---

## 1. Genel Bakış

**VLM-GATE** üç tarafı birbirine bağlayan **pasif bir köprü**:

- **LLM tarafı**: Trigger atar (kameranın hangi anında ne event olduğunu söyler).
  Sonuçları SSE üzerinden bizden okur.
- **VMS** (Video Management System): Kamera frame'lerini tutar, VLM'e gönderir.
- **VLM** (Vision-Language Model): Frame'i alır, doğal dil açıklaması üretir.

**Bizim iki temel sorumluluğumuz var:**

1. LLM'den gelen trigger'ı VMS'in beklediği şemaya çevirip iletmek.
2. VLM'in ürettiği description'ı LLM'in beklediği şemaya çevirip SSE üzerinden
   tüm bağlı LLM client'larına yaymak.

Ek olarak: VLM bize görseli base64 olarak gönderiyorsa, kendi disk'imize
kaydedip LLM'in HTTP üzerinden çekebileceği bir URL üretiyoruz.

**Önemli**: VLM'i biz çağırmıyoruz. VMS çağırıyor. VLM cevabı kendi config'inde
yazılı olan callback URL'imize push ediyor. Biz hep alıcı/iletici rolündeyiz.

---

## 2. Mimari ve Aktörler

```
┌──────────┐   POST /trigger    ┌──────────────┐
│   LLM    │ ─────────────────► │  VLM-GATE    │
│ (client) │                    │  (BİZ)       │
│          │ ◄───────GET /stream│  port 8000   │
│          │   (SSE, açık)      │              │
└──────────┘                    │              │
     ▲                          │              │
     │ GET /image?path=...      │              │
     └──────────────────────────│              │
                                │              │
                                │              │ POST /describe
                                │              │ (LLM trigger'ı VMS şemasında)
                                │              ▼
                                │       ┌───────────────┐
                                │       │     VMS       │
                                │       │ 172.20.14.110 │
                                │       │     :9812     │
                                │       └───────────────┘
                                │              │
                                │              │ frame'i bulur, base64 ile gönderir
                                │              ▼
                                │       ┌───────────────┐
                                │       │     VLM       │
                                │       │ 172.20.14.130 │
                                │       │     :18080    │
                                │       └───────────────┘
                                │              │
                                │              │ description üretir
                                │              │ POST /vlm-result
                                │              │ (callback_url config'den okunur)
                                │              │
                                └──────────────┘
                                  PUSH alındı, SSE'ye yayılır
```

### Aktörler ve Sorumlulukları

| Aktör | Sahibi | URL | Sorumluluk |
|---|---|---|---|
| **LLM** | LLM ekibi | Yok (client) | Trigger atar, SSE dinler, image API'sini çağırır |
| **VLM-GATE** | Furkan | `http://172.20.14.108:8000` | Köprü: trigger forward + result broadcast + image serve |
| **VMS** | Go Sejun (고세준) | `http://172.20.14.110:9812/describe` | Frame çekme, VLM'e gönderme |
| **VLM** | Park Hoonbeom (박훈범) | `http://172.20.14.130:18080/describe` | Description üretme + callback push (forwarder modülü) |

---

## 3. Akışın Hikâyesi

Bir trigger'ın yolculuğunu sırayla takip edelim:

### 3.1 — LLM Trigger Atar

LLM şunu POST'lar:

```http
POST http://172.20.14.108:8000/trigger
Content-Type: application/json

{
  "detected_time": "20260511102900",
  "type": "FIRE",
  "channel": 0,
  "node_id": 20061
}
```

### 3.2 — VLM-GATE'in Yaptığı

`main.py`'daki `trigger()` fonksiyonu:

1. `PENDING_TRIGGERS[20061]` sözlüğüne bu trigger'ı kaydeder. (Sonra
   eşleştirme için lazım — VLM cevabı geldiğinde "bu hangi trigger'a aitti"
   diye geri okuyacağız.)
2. `build_vms_payload()` ile şemayı dönüştürür:
   ```json
   {
     "vms": {"detail": {"node_id": 20061, "channel": 0}},
     "info": {"event": {"start_time": "20260511-102900"}}
   }
   ```
   Dikkat: `detected_time` (14 char) `start_time` (15 char, tire ile) oldu.
3. Bu payload'u `VMS_URL`'e POST eder.
4. VMS 200 dönerse, LLM'e şu cevabı verir:
   ```json
   {
     "status": "forwarded",
     "vms_url": "http://172.20.14.110:9812/describe",
     "vms_payload": { ... },
     "subscribers": 1
   }
   ```

### 3.3 — VMS'in Yaptığı (bizim görmediğimiz)

1. `node_id=20061` kamerası için, `start_time=20260511-102900` zamanındaki
   frame'i kendi disk arşivinden çeker.
2. Frame'i base64'ler.
3. VLM'in `/describe` endpoint'ine bu base64'le birlikte VLM'in beklediği
   payload'u POST eder.

### 3.4 — VLM'in Yaptığı

1. Frame'i alır, Qwen modelini çalıştırır, Korece bir description üretir.
2. Kendi config dosyasındaki `callback_url` değerini okur:
   ```
   callback_url = http://172.20.14.108:8000/vlm-result
   ```
3. Bu URL'e POST eder. Body olarak description doldurulmuş tam payload yollar.
   Image alanı genelde base64 olarak echo'lanır (VMS ne yolladıysa).

### 3.5 — VLM-GATE PUSH'u Alıyor

`main.py`'daki `vlm_result()` fonksiyonu:

1. Payload'dan `node_id`'yi çıkarır (`vms.detail.node_id`).
2. `PENDING_TRIGGERS[20061]` arar → bulur (Adım 3.2'de kaydetmiştik).
3. Image alanını çıkarır:
   - **Path geldiyse**: olduğu gibi kullanır.
   - **Base64 geldiyse**: `image_store.save_base64_image()` çağırır →
     `Z:\vlm_gate_cache\<sha256-16char>.jpg` olarak diske yazar →
     dosya yolunu döner.
4. Description'ı çıkarır.
5. `build_llm_payload()` ile LLM şemasında çıktı oluşturur:
   ```json
   {
     "detected_time": "20260511102900",
     "type": "FIRE",
     "channel": 0,
     "node_id": 20061,
     "data": [
       {
         "timestamp": "20260514054512",
         "description": "주거지역에서 화재 발생...",
         "api": "http://172.20.14.108:8000/image?path=Z%3A%5Cvlm_gate_cache%5Cabc.jpg"
       }
     ]
   }
   ```
6. Bu JSON'u tüm açık SSE subscriber'larına yayar (broadcast).

### 3.6 — LLM SSE Üzerinden Görüyor

LLM, daha önce `GET /stream` ile bağlanmış olduğu için aynı bağlantı
üzerinden bu event'i alır. Bağlantı **kapanmaz** — birden fazla trigger
arka arkaya gelirse hepsi aynı stream'de düşer.

### 3.7 — LLM İsterse Foto'yu İndirir

LLM, `data[0].api` URL'ini alıp tarayıcıdan veya kendi koduyla GET atar:
- `main.py`'daki `get_image()` fonksiyonu çalışır.
- `image_store.resolve_safe()` ile path'in `IMAGE_ROOT` altında olduğunu
  doğrular (güvenlik).
- `FileResponse` ile dosyayı bytes olarak döner.

---

## 4. Kurulum

### Gereksinimler

- Python 3.8+ (3.10+ olursa daha modern syntax'lar da çalışır ama gerek yok,
  her dosyada `from __future__ import annotations` kullandık).
- Network erişimi:
  - 172.20.14.110:9812 (VMS) — biz bu adrese POST atıyoruz.
  - 172.20.14.130:18080 (VLM) — sadece test scriptleri çağırıyor;
    asıl çalışmada VMS çağırıyor.
  - 172.20.14.108:8000 (biz) — VLM bu adrese push edebilmeli.
- `Z:\` mount edilmiş olmalı (image dosyaları bu drive üzerinde).

### Adımlar

```powershell
# Proje dizinine git
cd C:\Users\admin\fur\vlm_gate

# Bağımlılıkları kur
pip install -r requirements.txt

# .env dosyası oluştur
copy .env.example .env
# .env'i editör ile aç ve değerleri kontrol et (özellikle VLM_GATE_BASE_URL ve VMS_URL)
```

`requirements.txt` içeriği:
```
fastapi
uvicorn[standard]
sse-starlette
httpx
pydantic
python-dotenv
```

---

## 5. Konfigürasyon (.env)

`.env` dosyası `python-dotenv` tarafından otomatik yüklenir. Yoksa varsayılan
değerler `config.py`'dan okunur.

| Değişken | Default | Açıklama |
|---|---|---|
| `VLM_GATE_BASE_URL` | `http://127.0.0.1:8000` | **BİZİM** sunucumuzun dışarıdan görünen URL'i. LAN testinde kendi IP'in (örn `http://172.20.14.108:8000`) olmalı. SSE'deki `api` URL'i ve VLM'in göreceği callback URL bundan üretilir. |
| `VMS_URL` | `http://172.20.14.110:9812/describe` | VMS'in bizden trigger beklediği URL. `/trigger`'a gelen istekler şemayı çevirip buraya iletilir. |
| `VLM_URL` | `http://172.20.14.130:18080/describe` | Sadece `fake_vms.py` ve `probe_callback.py` kullanır. Asıl `main.py` VLM'i hiç çağırmaz. |
| `VLM_GATE_IMAGE_ROOT` | `Z:\` | `/image` endpoint'inin serve edebileceği klasörün kökü. Bunun dışındaki path istekleri 403 alır. |
| `VLM_GATE_IMAGE_CACHE` | `Z:\vlm_gate_cache` | VLM base64 image gönderirse buraya kaydedilir. **`IMAGE_ROOT` altında olmak zorunda.** |
| `SAMPLE_IMAGE_PATH` | `Z:\20260422\20897` | `fake_vms.py` örnek görsel kaynağı. Klasör verirse içinden rastgele bir jpg seçer. |

### .env Örneği (LAN testi için)

```ini
VLM_GATE_BASE_URL=http://172.20.14.108:8000
VMS_URL=http://172.20.14.110:9812/describe
VLM_URL=http://172.20.14.130:18080/describe
VLM_GATE_IMAGE_ROOT=Z:\
VLM_GATE_IMAGE_CACHE=Z:\vlm_gate_cache
SAMPLE_IMAGE_PATH=Z:\20260422\20897
```

---

## 6. Dosyalar — Birebir Açıklama

### `config.py`

Tek noktadan tüm URL/path config'i toplar. `python-dotenv` ile `.env`
yüklenir, sonra `os.getenv` ile okunur.

```python
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
```

`python-dotenv` kurulu değilse sessizce geçer (sadece env değişkenleri
ve default'lar kullanılır).

#### Değer Sıralaması (kritik)

```python
VLM_GATE_BASE_URL = os.getenv("VLM_GATE_BASE_URL", "http://127.0.0.1:8000")
```

Bu satır **"şuna bak, yoksa şunu kullan"** demektir:

1. **`.env` dosyasında** `VLM_GATE_BASE_URL=...` satırı varsa → onu kullan.
2. Yoksa **shell ortamında** `$env:VLM_GATE_BASE_URL` set edilmişse → onu kullan.
3. O da yoksa → ikinci parametre olan **default** (`http://127.0.0.1:8000`) kullanılır.

Yani **"hem env hem default"** gibi durmasına aldanma — gerçekte sadece
biri aktiftir, .env varsa default override edilir. Default'lar localhost
çünkü hiçbir şey set edilmediğinde bile servis ayağa kalksın diye güvence.

Tüm değişkenlerin açıklaması bir önceki bölümde (.env) verildi.

---

### `main.py`

VLM-GATE'in ana servisi. FastAPI uygulaması.

#### Modül seviyesinde tutulan state

```python
SUBSCRIBERS: list[asyncio.Queue] = []
PENDING_TRIGGERS: dict[int, dict[str, Any]] = {}
```

- **`SUBSCRIBERS`**: Açık SSE bağlantılarının kuyruğu listesi. Her bağlantı
  bir `asyncio.Queue` ile temsil edilir. `/vlm-result`'a push geldiğinde
  bu listede olan tüm kuyruklara payload yazılır (broadcast).
- **`PENDING_TRIGGERS`**: `node_id → trigger_data` sözlüğü. Trigger
  geldiğinde buraya yazılır, VLM push'u dönünce eşleştirme için okunur.

#### Pydantic Modeli

```python
class TriggerRequest(BaseModel):
    detected_time: str
    type: str
    channel: int
    node_id: int
```

LLM'in `/trigger`'a yollayacağı body'nin şekli. FastAPI bu modele uymayan
istekleri 422 ile reddeder.

#### Yardımcı Fonksiyonlar

##### `utc_now_compact()`

```python
def utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
```

Şu anki UTC zamanı `YYYYMMDDHHMMSS` formatında döner. SSE event'inin
`data[0].timestamp` alanı için kullanılır.

##### `llm_time_to_vms_time(t)`

```python
def llm_time_to_vms_time(t: str) -> str:
    if len(t) == 14 and t.isdigit():
        return f"{t[:8]}-{t[8:]}"
    return t
```

LLM `YYYYMMDDHHMMSS` (14 char) gönderir, VMS `YYYYMMDD-HHMMSS` (15 char,
8. karakterden sonra tire) bekler. Bu fonksiyon dönüşümü yapar. 14 karakter
ve hepsi rakam değilse olduğu gibi dön (defansif).

##### `build_vms_payload(req)`

```python
def build_vms_payload(req: TriggerRequest) -> dict:
    return {
        "vms": {"detail": {"node_id": req.node_id, "channel": req.channel}},
        "info": {"event": {"start_time": llm_time_to_vms_time(req.detected_time)}},
    }
```

LLM `TriggerRequest`'inden VMS payload'u üretir. `end_time` ve
`snapshot_period` opsiyonel — vermezsek VMS varsayılan davranır
(tek iframe, 10sn period).

##### `image_api_url(value)`

```python
def image_api_url(value: str) -> str:
    if not value:
        return ""
    return f"{config.VLM_GATE_BASE_URL}/image?path={quote(value, safe='')}"
```

Path string'inden tam image API URL'i üretir. URL-encoding (`quote`)
backslash, iki nokta, Korece karakterleri güvenle kapsar. Boş gelirse
boş döner.

Not: Base64 girdi gelmesin diye filtrelemiyoruz — çünkü `extract_image_path`
zaten base64'leri yakalayıp path'e çeviriyor. Buraya hep path geliyor.

##### `build_llm_payload(trigger, description, image_path)`

```python
def build_llm_payload(trigger, description, image_path) -> dict:
    return {
        "detected_time": trigger["detected_time"],
        "type": trigger["type"],
        "channel": trigger["channel"],
        "node_id": trigger["node_id"],
        "data": [{
            "timestamp": utc_now_compact(),
            "description": description,
            "api": image_api_url(image_path),
        }],
    }
```

LLM'in beklediği SSE event şemasını üretir. `data` neden liste — kontrat
böyle (LLM ekibi doğruladı), tek item olsa da liste içinde gelir.

##### `extract_node_id(vlm_payload)`, `extract_description(...)`, `extract_image_path(...)`

VLM push payload'undan ilgili alanları çıkarır. Hepsi try/except ile
korunmuş — alan yoksa boş/None döner. Bu sayede VLM şeması küçük
değişikliğe karşı dayanıklı.

`extract_image_path` özel: değer base64 ise `image_store.save_base64_image()`
çağırıp diske yazar, path döner. Console'a `[vlm-result] base64 cached → ...`
log'u atar.

#### Endpoint'ler

##### `POST /trigger`

Tek bir trigger'ı VMS'e relay eder.

```python
@app.post("/trigger")
async def trigger(req: TriggerRequest):
    PENDING_TRIGGERS[req.node_id] = req.model_dump()
    vms_payload = build_vms_payload(req)
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.post(config.VMS_URL, json=vms_payload)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise HTTPException(502, f"VMS forward failed: {e}")
    return {"status": "forwarded", "vms_url": ..., "vms_payload": ..., "subscribers": ...}
```

VMS 4xx/5xx dönerse 502 ile cevap. Cevapta `vms_payload` da dönüyor;
debug için faydalı, "ne gönderdik" gözle görmek için.

##### `POST /vlm-result`

VLM'in (kendi config'inden bizim URL'i okuyup) push ettiği endpoint.

```python
@app.post("/vlm-result")
async def vlm_result(payload: dict[str, Any]):
    node_id = extract_node_id(payload)
    desc_preview = extract_description(payload)[:60]
    print(f"[vlm-result] PUSH alındı node_id={node_id} desc='{desc_preview}...'")
    
    trigger = PENDING_TRIGGERS.get(node_id) if node_id is not None else None
    if trigger is None:
        trigger = {"detected_time": "", "type": "", "channel": 0, "node_id": node_id or 0}
        print(f"[vlm-result] WARN: no matching trigger for node_id={node_id}")
    
    out = build_llm_payload(
        trigger=trigger,
        description=extract_description(payload),
        image_path=extract_image_path(payload),
    )
    for q in list(SUBSCRIBERS):
        await q.put(out)
    return {"ok": True, "delivered_to": len(SUBSCRIBERS), "node_id": node_id}
```

Önemli noktalar:
- Trigger eşleşmezse de **yine de yayar** — sadece `detected_time`/`type`
  boş gelir. Bu, biz trigger atmadan başka biri (Park Hoonbeom test ettiğinde
  gördüğümüz gibi) doğrudan VLM'i tetiklerse çalışmaya devam etsin diye.
- `list(SUBSCRIBERS)` — kopya alıyor çünkü iterasyon sırasında client kopması
  durumunda listeyi modifiye edebiliriz.

##### `GET /stream`

LLM'in açtığı uzun süreli SSE bağlantısı.

```python
@app.get("/stream")
async def stream():
    queue: asyncio.Queue = asyncio.Queue()
    SUBSCRIBERS.append(queue)
    
    async def event_gen():
        try:
            counter = 0
            yield {"event": "connected", "data": json.dumps({"msg": "..."})}
            while True:
                payload = await queue.get()
                yield {
                    "event": "vlm_description",
                    "id": str(counter),
                    "data": json.dumps(payload, ensure_ascii=False),
                }
                counter += 1
        finally:
            if queue in SUBSCRIBERS:
                SUBSCRIBERS.remove(queue)
    
    return EventSourceResponse(event_gen())
```

- Her yeni bağlantıda kendine ait bir kuyruk oluşturur, SUBSCRIBERS listesine
  ekler.
- İlk olarak `connected` event'i atar (debug kolaylığı).
- Sonsuz döngüde kuyruğun `get()` ile bekler, gelen payload'u SSE event
  olarak yayar.
- `finally` bloğunda client kopunca kuyruğu listeden çıkarır (memory leak yok).
- `ensure_ascii=False`: Korece karakterler `\uXXXX` yerine doğal halinde
  görünsün diye.
- `id` alanı counter'dan üretilir — SSE protokolünde `Last-Event-ID`
  header'ıyla bağlantı koparsa kaldığı yerden devam etme imkanı verir
  (şu an kullanmıyoruz ama hazır).

##### `GET /image`

Path'ten foto serve eder.

```python
@app.get("/image")
async def get_image(path: str = Query(...)):
    try:
        p = image_store.resolve_safe(path)
    except image_store.UnsafePathError as e:
        raise HTTPException(403, str(e))
    if not p.is_file():
        raise HTTPException(404, "image not found")
    return FileResponse(p, media_type="image/jpeg")
```

- `resolve_safe` IMAGE_ROOT dışındaki path'leri 403 ile reddeder.
- Dosya yoksa 404.
- `FileResponse` Starlette'in dosya streaming response'u — hafıza dostu.
- `media_type="image/jpeg"` — uzantı kontrolü yapmıyoruz, hepsini jpeg
  olarak işaretliyoruz. Tarayıcı çoğunlukla doğru render eder. PNG için
  `image/png` istiyorsan uzantıdan inferans eklenebilir.

##### `DELETE /image`

Aynı path güvenliğiyle dosyayı siler.

```python
@app.delete("/image")
async def delete_image(path: str = Query(...)):
    try:
        p = image_store.resolve_safe(path)
    except image_store.UnsafePathError as e:
        raise HTTPException(403, str(e))
    if not p.is_file():
        raise HTTPException(404, "image not found")
    p.unlink()
    return {"status": "deleted", "path": str(p)}
```

LLM bir image'ı kullandıktan sonra temizlemek isterse çağırır.

##### `GET /health`

Servisin sağlık durumunu bildirir.

```python
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "subscribers": len(SUBSCRIBERS),
        "pending_triggers": list(PENDING_TRIGGERS.keys()),
        "vms_url": config.VMS_URL,
        "vlm_url": config.VLM_URL,
        "image_root": str(image_store.IMAGE_ROOT),
    }
```

Smoke testte ilk bakılacak yer. `pending_triggers` listesi boş değilse
trigger atılmış ama eşleşme bekleniyor demektir.

---

### `image_store.py`

Image dosyalarıyla ilgili tüm yardımcılar.

#### `IMAGE_ROOT`, `IMAGE_CACHE_DIR`

```python
IMAGE_ROOT = Path(config.IMAGE_ROOT).resolve()
IMAGE_CACHE_DIR = Path(config.IMAGE_CACHE_DIR).resolve()
IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
```

Modül yüklendiğinde cache klasörü yaratılır (yoksa).

#### `resolve_safe(path_str)`

```python
def resolve_safe(path_str: str) -> Path:
    p = Path(path_str).resolve()
    try:
        p.relative_to(IMAGE_ROOT)
    except ValueError as e:
        raise UnsafePathError(...) from e
    return p
```

Verilen string path'i çözümler ve `IMAGE_ROOT` altında mı diye kontrol eder.
`Path.resolve()` sembolik linkleri ve `..` benzeri çıkışları normalize eder
(yani `Z:\..\C:\Windows` → `C:\Windows`). `relative_to` ise IMAGE_ROOT
altında değilse `ValueError` atar — biz onu `UnsafePathError`'e çeviriyoruz.

Bu önlem olmadan `?path=C:/Windows/System32/...` ile arbitrary file read/delete
yapılabilirdi.

#### `looks_like_base64(value)`

```python
def looks_like_base64(value: str) -> bool:
    return bool(value) and value.startswith(("/9j/", "iVBOR"))
```

Heuristic: JPEG base64 `/9j/` ile, PNG base64 `iVBOR` ile başlar. Bu prefix'i
gören string'i base64 sayar. Tam doğru olmayabilir ama pratikte yetiyor —
gerçek path'ler `/9j/` ile başlamayacağından çakışma riski sıfır.

#### `save_base64_image(b64)`

```python
def save_base64_image(b64: str) -> Path:
    suffix = ".jpg" if b64.startswith("/9j/") else ".png" if b64.startswith("iVBOR") else ".bin"
    digest = hashlib.sha256(b64.encode("ascii")).hexdigest()[:16]
    out_path = IMAGE_CACHE_DIR / f"{digest}{suffix}"
    if not out_path.exists():
        out_path.write_bytes(base64.b64decode(b64))
    return out_path
```

- Suffix prefix'ten inferans (jpg/png/bin).
- Filename = base64 string'inin SHA256 hash'inin ilk 16 hex karakteri.
- Aynı içerik tekrar gelirse aynı dosyaya yazılır → deduplication, disk
  şişmez. Çünkü `if not out_path.exists()`.
- Hash uzunluğu 16 hex (8 byte) → çakışma olasılığı çok düşük (2^64).

---

### `fake_vms.py`

Geliştirme aşamasında, gerçek VMS hazır değilken kullandığımız taklit servis.
**Gerçek VMS aktif olunca artık gerek yok**, ama bağımsız test için kalsın
diye duruyor.

Port: 8001 (`uvicorn fake_vms:app --port 8001 --reload`).

#### `pick_sample_image()`

`SAMPLE_IMAGE_PATH` config'i klasörse içinden rastgele bir jpg/png/jpeg
seçer, dosyaysa kendisini döner. Yoksa `FileNotFoundError`.

#### `FromGate` Modeli

```python
class FromGate(BaseModel):
    node_id: int
    type: str
    detected_time: str
    channel: int
```

VLM-GATE'in eski versiyonunda fake_vms'e bu şekilde gönderiyorduk. Şimdi
gerçek VMS farklı şema kullanıyor (`vms.detail.node_id` vb.) — fake_vms
güncellenmedi çünkü artık prod yolundan çekildi.

#### `build_vlm_input(req, image_b64)`

VLM'in beklediği payload'u üretir. `callback_url` payload'da YOK çünkü
VLM kendi config'inden okuyor.

#### `from_vlm_gate` Endpoint'i

```python
@app.post("/from-vlm-gate")
async def from_vlm_gate(req: FromGate):
    image_path = pick_sample_image()
    image_b64 = load_image_b64(image_path)
    vlm_input = build_vlm_input(req, image_b64)
    async with httpx.AsyncClient(timeout=120) as client:
        vlm_resp = await client.post(config.VLM_URL, json=vlm_input)
        vlm_resp.raise_for_status()
    return {"ok": True, "vlm_status": vlm_resp.status_code, ...}
```

VLM-GATE'in trigger'ını alır, VLM'i çağırır, biter. Biz aradan çekildik —
VLM cevabı zaten doğrudan VLM-GATE'in `/vlm-result`'una push edecek
(VLM forwarder modülü sayesinde).

---

### `listen_sse.py`

SSE bağlantısı açıp gelen event'leri ekrana basan test client'ı (LLM rolü).

#### Modlar

- `python listen_sse.py` → ham SSE çıktısı (telde ne giderse), debugging için
- `python listen_sse.py --pretty` → JSON parse edilip pretty-print, base64
  gibi uzun string'ler maskelenir

#### `mask_long_strings(obj, limit=120)`

Recursive: string 120 karakterden uzunsa `<NN chars: ABCDEF...>` formatında
maskele. Dict ve list'in içine girip aynısını uygular. Base64 image alanları
gibi terminal'i kirletecek şeyleri okunaklı tutar.

#### `print_pretty(event, event_id, raw_data)`

Event ismine göre format değiştirir:
- `vlm_description` → header + masklenmiş JSON
- `connected` → tek satır
- diğer → tek satır JSON

#### `stream_pretty` ve `stream_raw`

İki ayrı async fonksiyon, mod parametresine göre seçilir. Pretty olan SSE
satırlarını parse edip yukarıdaki helper'ı çağırır; raw olan satırları
olduğu gibi basar.

---

### `trigger.py`

LLM rolünde trigger atan CLI script.

#### Argümanlar

```
--node-id        int     default 20887
--type           str     default FIRE
--channel        int     default 0
--detected-time  str     default şimdiki UTC time (YYYYMMDDHHMMSS)
--count          int     default 1     (kaç trigger atılacak)
--delay          float   default 0.0   (trigger arası saniye)
```

Kullanım örnekleri:
```powershell
# Tek trigger, default değerlerle
python trigger.py

# Spesifik node, event, zaman
python trigger.py --node-id 20061 --type FIRE --detected-time 20260511102900

# 5 trigger, 2sn arayla, node_id artarak
python trigger.py --count 5 --delay 2
```

#### Akış

Her iterasyonda:
1. `detected_time` belirlenir (CLI veya şimdiki UTC).
2. `node_id` artırılır (her trigger farklı kamera).
3. `POST /trigger` atılır.
4. Sunucu cevabını ekrana basar.

---

---

## 7. Endpoint Referansı

### `POST /trigger`

**Kim çağırır:** LLM tarafı.

**Body:**
```json
{
  "detected_time": "20260511102900",
  "type": "FIRE",
  "channel": 0,
  "node_id": 20061
}
```

**Response (200):**
```json
{
  "status": "forwarded",
  "vms_url": "http://172.20.14.110:9812/describe",
  "vms_payload": {
    "vms": {"detail": {"node_id": 20061, "channel": 0}},
    "info": {"event": {"start_time": "20260511-102900"}}
  },
  "subscribers": 1
}
```

**Hatalar:**
- `422`: Body şeması yanlış (alan eksik/yanlış tip).
- `502`: VMS'e ulaşılamadı veya 4xx/5xx döndü.

---

### `POST /vlm-result`

**Kim çağırır:** VLM (kendi config'indeki callback URL üzerinden).

**Body:** VLM'in standart cevap şeması — `info.event.description`,
`info.event.image`, `vms.detail.node_id` alanlarını kullanırız, gerisi
yoksayılır.

**Response (200):**
```json
{
  "ok": true,
  "delivered_to": 1,
  "node_id": 20061
}
```

`delivered_to` o anda kaç SSE subscriber'a yayıldığını söyler. 0 ise
kimse dinlemiyor demektir (push yine de işlendi, image cache'lendi).

---

### `GET /stream`

**Kim çağırır:** LLM tarafı.

**Headers:** Genelde `Accept: text/event-stream` eklenir, ama biz
EventSourceResponse zaten doğru content-type'ı dönüyor.

**Response:** SSE stream, kapanmaz. Her event:

```
event: vlm_description
id: 0
data: {"detected_time": "...", "type": "...", "channel": ..., "node_id": ..., "data": [{...}]}

```

İlk event her zaman:
```
event: connected
data: {"msg": "subscribed to VLM stream"}
```

---

### `GET /image?path=...`

**Kim çağırır:** LLM (SSE event'inde gelen `api` URL'i ile).

**Query:** `path` — image dosyasının tam path'i (URL-encoded).

**Response:**
- `200` + JPEG bytes → dosya gönderilir.
- `403` → path IMAGE_ROOT dışında.
- `404` → dosya yok.

---

### `DELETE /image?path=...`

**Kim çağırır:** LLM (image kullanıldıktan sonra temizlemek için).

**Query:** `path`.

**Response:**
- `200`: `{"status": "deleted", "path": "..."}`
- `403`: IMAGE_ROOT dışında.
- `404`: dosya yok.

---

### `GET /health`

**Kim çağırır:** Manuel debug.

**Response:**
```json
{
  "status": "ok",
  "subscribers": 1,
  "pending_triggers": [20061],
  "vms_url": "http://172.20.14.110:9812/describe",
  "vlm_url": "http://172.20.14.130:18080/describe",
  "image_root": "Z:\\"
}
```

---

## 8. Şema Dönüşümleri

### LLM → VLM-GATE (`/trigger` body)

```json
{
  "detected_time": "YYYYMMDDHHMMSS",
  "type": "FIRE",
  "channel": 0,
  "node_id": 20061
}
```

### VLM-GATE → VMS (forwarded body)

```json
{
  "vms": {"detail": {"node_id": 20061, "channel": 0}},
  "info": {"event": {"start_time": "YYYYMMDD-HHMMSS"}}
}
```

Dönüşüm noktaları:
- `detected_time` (14 char) → `start_time` (15 char, 8. karakterden sonra `-` eklendi)
- `type` ve `detected_time` orijinaller VMS'e geçirilmez (VMS kullanmıyor),
  PENDING_TRIGGERS'da saklanır.

### VLM → VLM-GATE (`/vlm-result` body — örnek)

```json
{
  "info": {
    "event": {
      "description": "주거지역에서 화재 발생...",
      "image": "/9j/4AAQ..." (base64) veya "Z:\\path\\to\\file.jpg",
      "type": "...",
      "detected_time": "...",
      "start_time": "...",
      "end_time": "...",
      "snapshot_period": 10
    }
  },
  "vms": {
    "detail": {"node_id": 20061, "channel": 0, ...},
    "type": "danusys"
  },
  "LLM": "url",
  "VLM": "...",
  "service_name": "Ainos1",
  "version": 1001
}
```

VLM-GATE bundan sadece üç alanı çeker:
- `vms.detail.node_id` — eşleştirme için
- `info.event.description` — LLM'e iletilecek metin
- `info.event.image` — base64 ise cache'lenir, path ise direkt URL

### VLM-GATE → LLM (SSE event `vlm_description`)

```json
{
  "detected_time": "20260511102900",
  "type": "FIRE",
  "channel": 0,
  "node_id": 20061,
  "data": [
    {
      "timestamp": "20260514054512",
      "description": "주거지역에서 화재 발생...",
      "api": "http://172.20.14.108:8000/image?path=Z%3A%5Cvlm_gate_cache%5Cabc.jpg"
    }
  ]
}
```

`detected_time`, `type`, `channel`, `node_id` PENDING_TRIGGERS'tan eşleşir.
`description` ve `api` VLM'in cevabından üretilir. `timestamp` push'un
geldiği UTC anı.

---

## 9. Image Akışı

### İki olası senaryo

1. **VMS path gönderir** (ideal):
   - VMS, VLM'e payload yollarken `info.event.image` alanına dosya yolu
     koyar (örn `Z:\20260422\20897\foo.jpg`).
   - VLM bu path'i echo eder.
   - Bizim `extract_image_path` path görür, olduğu gibi döner.
   - `image_api_url` URL üretir.
   - LLM URL'e GET atar, `/image` endpoint'i diskten dosyayı serve eder.

2. **VMS base64 gönderir** (test sırasında olan):
   - Image base64 olarak VLM'e gider, VLM aynı base64'ü echo eder.
   - Bizim `extract_image_path` base64 görür, `save_base64_image` çağırır.
   - SHA256-bazlı isimle `Z:\vlm_gate_cache\<hash>.jpg` olarak diske yazılır.
   - Bu path'ten URL üretilir.
   - LLM URL'e GET atar, `/image` endpoint'i cache klasöründen serve eder.

### Cache deduplication

Aynı base64 tekrar gelirse aynı dosyaya yazılır (hash collision pratikte
yok — 2^64 olasılık). `save_base64_image` `if not out_path.exists()` kontrolü
ile gereksiz I/O'dan kaçınır.

### Cache temizliği

Şu an otomatik temizleme **YOK**. `Z:\vlm_gate_cache\` zamanla büyür.
Çözümler:
- LLM `DELETE /image?path=...` ile silebilir.
- Periyodik cron / scheduled task ile X gün önceki dosyalar silinebilir.
- Şimdilik manuel temizlik.

---

## 10. Çalıştırma Senaryoları

### Senaryo A — Sadece geliştirme (her şey localhost)

`.env`:
```
VLM_GATE_BASE_URL=http://127.0.0.1:8000
VMS_URL=http://127.0.0.1:8001/from-vlm-gate
```

Terminal 1:
```powershell
uvicorn main:app --port 8000 --reload
```

Terminal 2:
```powershell
uvicorn fake_vms:app --port 8001 --reload
```

Terminal 3:
```powershell
python listen_sse.py --pretty
```

Terminal 4:
```powershell
python trigger.py
```

### Senaryo B — LAN testi (gerçek VLM, fake VMS)

`.env`:
```
VLM_GATE_BASE_URL=http://172.20.14.108:8000
VMS_URL=http://127.0.0.1:8001/from-vlm-gate
```

Terminal 1:
```powershell
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

(Diğerleri Senaryo A ile aynı.)

### Senaryo C — Tam prod akışı (gerçek VMS + gerçek VLM)

`.env`:
```
VLM_GATE_BASE_URL=http://172.20.14.108:8000
VMS_URL=http://172.20.14.110:9812/describe
```

Terminal 1:
```powershell
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Terminal 2:
```powershell
python listen_sse.py --pretty
```

Terminal 3:
```powershell
python trigger.py --node-id 20061 --channel 0 --type FIRE --detected-time 20260511102900
```

Fake VMS'e gerek yok (kapalı kalır).

---

## 11. Uçtan Uca Test

### Adım 1 — Servisi başlat

```powershell
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Beklenen ilk çıktı:
```
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     Started reloader process
INFO:     Started server process
INFO:     Waiting for application startup.
INFO:     Application startup complete.
```

### Adım 2 — Health check

Yeni terminal:
```powershell
curl http://172.20.14.108:8000/health
```

Beklenen:
```json
{"status":"ok","subscribers":0,"pending_triggers":[],"vms_url":"...","vlm_url":"...","image_root":"Z:\\"}
```

`subscribers=0` — henüz LLM bağlanmadı.

### Adım 3 — Listener bağla

```powershell
python listen_sse.py --pretty
```

Çıktı:
```
[listen] connecting → http://172.20.14.108:8000/stream
[listen] mode = pretty

[connected] {'msg': 'subscribed to VLM stream'}
```

Health'i tekrar sorgula → `subscribers=1` olmalı.

### Adım 4 — Trigger at

Yeni terminal:
```powershell
python trigger.py --node-id 20061 --channel 0 --type FIRE --detected-time 20260511102900
```

Çıktı:
```
[trigger] POST http://172.20.14.108:8000/trigger {'detected_time': '20260511102900', ...}
[trigger] ← 200 {"status":"forwarded","vms_url":"...","vms_payload":{...},"subscribers":1}
```

### Adım 5 — Servis log'unda trigger'ı gör

Terminal 1 (uvicorn) yeni satır:
```
INFO:     127.0.0.1:xxxxx - "POST /trigger HTTP/1.1" 200 OK
```

### Adım 6 — Bekle (3-10 saniye)

VMS arşivden frame çekiyor, VLM Qwen modelini çalıştırıyor, push hazırlıyor.

### Adım 7 — Push log'unu gör

Terminal 1 yeni satırlar:
```
[vlm-result] PUSH alındı node_id=20061 desc='주거지역에서 화재 발생...'
[vlm-result] base64 cached → Z:\vlm_gate_cache\abc123def456.jpg
INFO:     172.20.14.130:xxxxx - "POST /vlm-result HTTP/1.1" 200 OK
```

Kaynak IP `172.20.14.130` (VLM) görmen önemli — gerçekten VLM'den geldi.

### Adım 8 — Listener event'ini gör

Listener terminali yeni blok:
```
=== event=vlm_description id=0 ===
{
  "detected_time": "20260511102900",
  "type": "FIRE",
  "channel": 0,
  "node_id": 20061,
  "data": [
    {
      "timestamp": "20260514054512",
      "description": "주거지역에서 화재 발생으로 인해 연기 및 구호 활동이 진행 중입니다.",
      "api": "http://172.20.14.108:8000/image?path=Z%3A%5Cvlm_gate_cache%5Cabc123def456.jpg"
    }
  ]
}
```

`detected_time` ve `type` dolu (PENDING_TRIGGERS eşleşti).

### Adım 9 — Foto'yu görüntüle

`api` URL'ini tarayıcıya yapıştır → JPEG açılmalı.

Veya:
```powershell
dir Z:\vlm_gate_cache\
```
Yeni `.jpg` dosyası listede olmalı.

### Adım 10 — Birden fazla trigger denemek

Listener açık kaldıkça:
```powershell
python trigger.py --count 5 --delay 3 --node-id 20061
```

Listener'da 5 farklı event görmelisin (node_id'ler 20061, 20062, ..., 20065).

---

## 12. Sorun Giderme

| Belirti | Olası sebep | Çözüm |
|---|---|---|
| `uvicorn main:app` `ModuleNotFoundError: fastapi` | Bağımlılık eksik | `pip install -r requirements.txt` |
| Sunucu ayağa kalkıyor ama `0.0.0.0` yerine `127.0.0.1` çıkıyor | `--host 0.0.0.0` flag'i unutuldu | Komutu `--host 0.0.0.0` ile başlat |
| `curl http://172.20.14.108:8000/health` Connection refused | Sunucu localhost'ta dinliyor, dış arayüz açık değil | Aynı yukarıdaki |
| `curl` LAN'dan timeout veriyor | Windows Firewall 8000 portunu blokluyor | `wf.msc` → Inbound → Yeni Kural → TCP 8000 → Allow |
| `POST /trigger` 502 "VMS forward failed" | VMS adresine ulaşılamıyor | (1) VMS_URL doğru mu? (2) VMS ayakta mı? (3) Network/firewall? Test: `curl -X POST http://172.20.14.110:9812/describe -d "{}" -H "Content-Type: application/json"` |
| `POST /trigger` 422 | Body şeması yanlış | Pydantic hatası mesajındaki alana bak |
| Trigger 200 dönüyor ama hiç push gelmiyor | (a) VLM config'de callback_url yok (b) Network sorun | (a) Park Hoonbeom'a "config'inize callback_url ekleyip restart edin" de. (b) VLM makinesinden senin adresine ping/curl atılabiliyor mu kontrol |
| Push geldi ama listener'da event yok | Listener kopmuş veya yeniden başlatılmamış | listen_sse.py'ı kapat, tekrar aç |
| Push'da `WARN: no matching trigger` | VLM cevabı bizim atmadığımız bir trigger için (başka birinin testi) | Normal — biz trigger atarsak eşleşir |
| `api` alanı boş | VLM cevabında image alanı yok veya tanınmayan format | VLM cevabı debug et: `print(payload)` `extract_image_path` öncesi ekle |
| `api` URL'i tarayıcıda 403 "outside root" | Path IMAGE_ROOT dışında | `IMAGE_ROOT` config'i geniş tut (örn `Z:\` tüm Z drive) |
| `api` URL'i tarayıcıda 404 | Cache dosyası silinmiş veya yazılamamış | `Z:\vlm_gate_cache\` klasörü var mı, yazma izni var mı |
| Cache klasörü şişti | Otomatik temizlik yok | Manuel sil veya cron yaz |
| SSE event'leri Korece çıkmıyor `\uXXXX` görünüyor | `ensure_ascii=False` parametresi atlanmış | `main.py`'da `json.dumps`'a `ensure_ascii=False` ekle (zaten var, kontrol et) |
| Listener pretty mode'da hata | JSON parse hatası, gelen veri JSON değil | Listener `--pretty` olmadan çalıştır, ham SSE'yi gör |

### Hızlı network doğrulama

VLM makinesinden bizim sunucuya erişim:
```powershell
# VLM makinesinde
curl http://172.20.14.108:8000/health
```

Bizim makineden VMS'e erişim:
```powershell
curl http://172.20.14.110:9812/health
```
(VMS'in /health'i olmayabilir, o zaman 404 da OK — bağlantı kuruluyor demektir.)

Bizim makineden VLM'e erişim:
```powershell
python probe_callback.py
```

---

## 13. Ekibe Verilecek URL'ler

### LLM ekibine

> Trigger atın: `POST http://172.20.14.108:8000/trigger`
> Body şeması:
> ```json
> {"detected_time": "YYYYMMDDHHMMSS", "type": "FIRE", "channel": 0, "node_id": 20061}
> ```
>
> Sonuçları SSE üzerinden alın: `GET http://172.20.14.108:8000/stream`
> Bağlantı uzun süreli açık kalır, art arda gelen tüm sonuçları aynı stream'de
> alırsınız.
>
> Foto'yu çekin: SSE event'indeki `data[0].api` alanı tam URL veriyor,
> doğrudan kullanın.
>
> Foto'yu silmek isterseniz: `DELETE http://172.20.14.108:8000/image?path=...`

### Park Hoonbeom (VLM)

> VLM forwarder modülünüzün config'ine callback_url ekleyin:
> ```
> callback_url = http://172.20.14.108:8000/vlm-result
> ```
> Ekledikten sonra VLM servisini restart edin. Bu sayede VLM her cevabını
> doğrudan bize push eder.

### Go Sejun (VMS)

> Bizden gelen trigger şu şemada olacak:
> ```json
> {
>   "vms": {"detail": {"node_id": 20061, "channel": 0}},
>   "info": {"event": {"start_time": "YYYYMMDD-HHMMSS"}}
> }
> ```
> URL'imiz: `http://172.20.14.108:8000/trigger` (bizden VLM-GATE'e değil,
> doğrudan VMS'inize gelen şema). callback_url ile uğraşmanıza gerek yok —
> VLM kendi config'inden okuyor.

---

## 14. Gelecek İyileştirmeler

### Kısa vadede

- [ ] Image cache temizliği — TTL veya max size bazlı
- [ ] LLM bağlantı kopunca PENDING_TRIGGERS'da TTL — eski eşleşmemiş trigger'lar
      sonsuza kadar memory'de kalmasın
- [ ] Daha iyi log — structlog veya benzeri, JSON log
- [ ] Health endpoint'inde son N push'un istatistiği

### Orta vadede

- [ ] VLM cevabında image **path** olarak gelirse base64 cache'lemeyi atla
      — şu an her durumda base64 yolundan geçiyor, verimsiz değil ama
      gereksiz I/O
- [ ] SSE'de `Last-Event-ID` desteği — client kopup tekrar bağlanırsa kaldığı
      yerden devam etsin
- [ ] Multiple LLM client filtering — her client sadece istediği `node_id`/
      `type`'ları alsın (şu an broadcast)
- [ ] Auth — şu an açık endpoint'ler. API key veya bearer token eklenebilir.

### Uzun vadede

- [ ] Replace Python `dict` based PENDING_TRIGGERS with Redis (multi-replica
      deploy için)
- [ ] Prometheus metrics endpoint
- [ ] Docker compose dosyası — VLM-GATE + fake_vms + listener bir komutla

---

## Ek — Hızlı Komut Cheatsheet

```powershell
# Kurulum
pip install -r requirements.txt
copy .env.example .env

# Ana servis (production)
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# Fake VMS (sadece dev)
uvicorn fake_vms:app --port 8001 --reload

# SSE listener
python listen_sse.py            # raw
python listen_sse.py --pretty   # masklenmiş JSON

# Trigger
python trigger.py
python trigger.py --node-id 20061 --channel 0 --type FIRE --detected-time 20260511102900
python trigger.py --count 5 --delay 2

# Health check
curl http://172.20.14.108:8000/health

# Image manuel test
curl -o test.jpg "http://172.20.14.108:8000/image?path=Z:\vlm_gate_cache\<hash>.jpg"

# Image silme manuel test
curl -X DELETE "http://172.20.14.108:8000/image?path=Z:\vlm_gate_cache\<hash>.jpg"

# Cache içeriği
dir Z:\vlm_gate_cache\
```

---

**Son güncelleme:** 2026-05-14
**Sahibi:** Furkan
**Slack/email questions:** ekipteki ilgili kişiye sor (Park Hoonbeom — VLM, Go Sejun — VMS)
