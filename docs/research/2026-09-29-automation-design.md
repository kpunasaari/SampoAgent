# SampoAgent otomasyon dönüşümü: başlangıç analizi (güncel durum eki aşağıdadır)

Tarih: 2026-09-29. Durum: tasarım önerisi; uygulanmış özellik listesi değildir.
Kapsam: mevcut yerel Python/FastAPI/SQLite uygulaması. Barındırılan çok kullanıcılı
SaaS bu tasarımın varsayımı değildir. Her kurulum kendi aday verisini tutar.

## 1. Amaç ve başarı tanımı

Kullanıcı profilini doğrular, önerilen meslekleri seçer ve çalışma sınırlarını
belirler. Sistem ilan arar, uygunluğu açıklar, gerçek bilgilere dayalı evrak hazırlar,
desteklenen formları doldurur ve sonucu kanıtla takip eder. Kullanıcı yalnızca
eksik/çelişkili bilgi, özel beyan veya erişim engeli olduğunda devreye girer.
Tam otomasyon bütün sitelerde sıfır müdahale garantisi değildir.

Mevcut AGENTS.md ve skill kuralları her son gönderimde başvuruya özel onay ister.
Bu analiz bu kuralı kaldırmaz ve herhangi bir canlı başvuru göndermez. Gelecekte
gözetimsiz gönderim açılacaksa kod, UI, dokümantasyon ve skill politikaları aynı
yetki modeline geçirilmelidir; yalnızca bir ayar adını değiştirmek yeterli değildir.

## 2. Kodda doğrulanan durum ve eksikler

| Alan | Mevcut kanıt | Gerekli değişiklik |
|---|---|---|
| Profil soruları | `candidate/questions.py`, `app/onboarding.py`: 63 yerel taslak cevap | Tipli alanlar, tekrar eden kayıtlar, onay, sürüm, kapsam, cevabı forma bağlama |
| CV analizi | `candidate/service.py`: metin çıkarımı, e-posta ve dört İngilizce başlık | FI/EN başlıkları, tarihler, deneyim/eğitim, kaynak metin aralıkları, çelişki incelemesi; isteğe bağlı OCR |
| Meslek önerisi | `careers/recommendations.py`: beş meslek, kelime kesişimi | Sürümlü beceri/meslek taksonomisi, eş anlamlılar, açıklanabilir öneri ve zorunlu belge ayrımı |
| Arama | `jobs/runner.py`: feed/API/statik Scrapling; çalıştırma başına 8 otomatik kaynak | Kalıcı zamanlama, kaynaklar arasında adil sıra, güncellik ve canonical ilan doğrulama |
| Başvuru kuyruğu | `app/main.py`: hazırlama ve elle durum değiştirme | Kalıcı iş motoru, geçiş kuralları, atomik rezervasyon ve yeniden başlama |
| Tarayıcı | `agents/browser.py`: Protocol ve UnconfiguredBrowserAgent | Gerçek adaptör, form şeması, yükleme, alan doğrulama, çok adımlı akış ve kanıt |
| Otomasyon politikası | `applications/workflow.py`: mod enumları ve basit risk kontrolü | Her dış işlem öncesi tek merkezi yetki kararı; mod adı başarı kanıtı değildir |
| Cevap bankası | `applications/answers.py`: kayıt ve overwrite koruması | Anlamsal alan kimliği, kaynak/kapsam/tarih kontrolü, eksik bilgi kuyruğu |
| E-posta | `integrations/email_oauth.py`, `mailbox.py`: Gmail/Outlook okuma | Ayrı gönderim izni, çoklu hesap, gönderim outbox ve sonuç uzlaştırma |
| Tekilleştirme | İlan fingerprint ve uygulama düzeyinde kayıt kontrolü | Eşzamanlı worker için DB unique constraint ve dış işlem deneme kimliği |
| Takip | Elle durum, zaman çizelgesi, kanıt ve olası e-posta yanıtları | Elle bildirilen/otomatik doğrulanan ayrımı; yanlış eşleşmede kullanıcı incelemesi |
| İşletim | Yerel uygulama | Migration, yedek/geri yükleme, worker sağlığı, durdurma, redakte log, sürüm uyumu |

Önceki 165 testin geçmesi canlı ATS veya gözetimsiz gönderimin çalıştığını göstermez.
Bu tur kaynak kod incelemesidir; canlı OAuth/ATS entegrasyonları test edilmemiştir.

## 3. Mimari seçenekler

1. **Önerilen: yerel hibrit motor.** Python iş motoru + deterministik politikalar +
   ATS adaptörleri + isteğe bağlı tarayıcı ajanı. Mevcut yatırımı korur; bilgisayarın
   açık kalması gerekir. Ajan bağlantısı yoksa açıkça bekler.
2. **Yalnızca konuşma içindeki ajan.** İlk destek hızlıdır, ancak sohbet oturumu
   kalıcı worker değildir; kesintiler ve izlenebilirlik zayıftır.
3. **Bulut SaaS.** Sürekli çalışabilir; tenant izolasyonu, kimlik doğrulama,
   güvenli tarayıcı oturumları ve veri işletimi ayrı proje gerektirir.

İlk sürümde FastAPI ve SQLite korunur. Her worker kendi DB bağlantısını açar;
tek process içindeki paylaşılan bağlantı çoklu worker tasarımı olarak kullanılmaz.
Şema değişiklikleri sürümlü migration ile, mevcut aday verisini silmeden yapılır.

## 4. Uçtan uca akış

Profil taslağı → kaynaklarla doğrulama → meslek önerileri → kullanıcı hedef seçimi
→ kaynaklardan keşif → ilan/son tarih doğrulaması → zorunlu şart elemesi
→ başvuru paketi → form keşfi → cevap çözümleme → yetki kontrolü
→ gönderim denemesi → kanıt uzlaştırma → takip.

Eksik cevap akışı: NEEDS_INPUT → kullanıcı cevabı → kaynağıyla doğrulama → yalnızca
etkilenen başvuruları yeniden değerlendirme. Aynı kapsamlı soru tekrar sorulmaz;
başka ülkeye/işverene ait cevap sessizce taşınmaz.

Başvuru durumları: DISCOVERED, VALIDATED, MATCHED, PREPARING, NEEDS_INPUT,
NEEDS_AUTH, NEEDS_REVIEW, READY, SUBMITTING, SUBMITTED_UNVERIFIED,
SUBMITTED_CONFIRMED, FAILED_RETRYABLE, FAILED_FINAL, EXPIRED, CANCELLED.
Mülakat/teklif/ret ayrı takip olaylarıdır. Elle APPLIED işaretlemek doğrulanmış
gönderimle aynı durum olmamalıdır.

## 5. Profil, beceri ve cevap tasarımı

- Her cevap: stable field ID, tip, değer, dil, kaynak, doğrulayan kişi/zaman,
  profil sürümü, ülke/işveren/ilan kapsamı, geçerlilik sonu, hassasiyet.
- Cevap durumları: unknown, draft, confirmed, declined, expired, conflicted.
  Boş cevap hiçbir zaman No değildir. Tercih ile olgusal iddia ayrı tutulur.
- Sorular meslek/ülke/önceki cevaplara göre görünür; geri dönünce taslak kaybolmaz.
  Tarih/ücret/dil seviyesi tipli alan olur; deneyim/eğitim tekrar eden kayıtlardır.
- CV ve anket çelişirse eski değer silinmez; kullanıcı çözene kadar ilgili iddia
  kullanılmaz. Belge değişince bağlı başvuru paketlerinin geçerliliği yeniden kontrol edilir.
- ESCO için sürümlü yerel indeks, kaynak/lisans bildirimi ve güncelleme mekanizması
  tasarlanır. Teknik olmayan yetenekler de eşleştirilir; hobi profesyonel deneyim
  sayılmaz. Öneri, eksik zorunlu lisansı varmış gibi göstermez.
- Cevap çözümleyici önce doğrulanmış tipli kayda bakar. Dil/ifade dönüşümünde yeni
  iddia eklenmez. Motivasyon metni yalnızca aday ve ilan kanıtlarından üretilir.
- Öğrenilen ATS alan eşleştirmeleri kişisel verisiz, sürümlü ve fixture testlidir;
  bir kullanıcı cevabı diğer kullanıcıların varsayılanı olmaz.

## 6. Gönderim ve yetki sınırları

Dry Run: veri aktarımı/gönderim yok; simülasyon. Yarı otomatik: paket bazında onay.
Gözetimsiz mod hedef tasarımı: süreli ve geri alınabilir kapsamlı yetki; izin verilen
meslekler, ülkeler, kaynaklar, kanallar, ücret/saat koşulları ve günlük kota.
Yeni veya kapsam dışı soru yetkiyi genişletmez. Mevcut özel onay kuralı, yeni politika
ayrıca kararlaştırılıp uçtan uca uygulanana kadar korunur.

Her gönderim öncesi paket hash'i, profil sürümü, ilan durumu, hedef domain, ekler,
gönderici hesabı ve geçerli izin kontrol edilir. Bir alan değişirse eski paket onayı
geçersiz olur. Pause/izin iptali kuyruktaki işler için de etkilidir.

CAPTCHA, MFA ve oturum açma kullanıcıya devredilir; erişim kontrolü aşılmaz.
İş teklifi kabulü, sözleşme imzası, ödeme, resmi beyan ve tıbbi/adli açıklamalar
genel otomatik başvuru yetkisine dahil değildir. Test/assessment aday adına çözülmez.

## 7. Worker, adaptörler ve gönderim güvenilirliği

- `automation/worker.py`: kalıcı kuyruk, lease, heartbeat, iptal ve checkpoint.
- `automation/policy.py`: her dış işlemde aynı yetki/risk kararı.
- `applications/resolver.py`: answer / ask / block kararı ve kaynak gerekçesi.
- `agents/forms.py`: alan ID, label, tip, required, seçenek, upload kısıtları,
  koşullu alanlar ve form sürümü; yalnızca tahmini koordinata güvenilmez.
- ATS adaptörleri önce izinli resmi entegrasyon, sonra desteklenen tarayıcı akışı.
  İlk adaylar mevcut kullanımda görülen Laura/ReachMee/Likeit; gerçek destek ancak
  fixture, staging ve kullanıcı denetimli canlı kanıt sonrasında ilan edilir.
- Read işlemleri sınırlı exponential backoff ve Retry-After ile tekrar denenebilir.
  Gönderim sonrası timeout: SUBMITTED_UNVERIFIED; kör tekrar gönderim YOK.
- Bir aday/canonical ilan için atomik benzersiz başvuru; attempt/outbox kimlikleri.
  Üçüncü taraf idempotency sağlamıyorsa exactly-once vaat edilmez.
- Kaynak başına kota ve dönen cursor; 8 kaynak sınırını kaldırıp aynı kaynakları
  sınırsız döndürmek yerine her kaynağın sırası gelir.
- İşçi kapanıp açıldığında SUBMITTING işler önce uzlaştırılır. Uyku sonrası tüm
  kaçırılan işleri aynı anda göndermek yerine süre/kota tekrar değerlendirilir.

## 8. E-posta, güvenlik ve kullanıcı ekranları

Gmail gönderimi gmail.send, Microsoft delegated Mail.Send yetkisi ister. Okuma
bağlantısını sessizce genişletmeyiz; kullanıcı gönderim hesabını ayrı bağlar.
Uygulamaya giriş yapmak ile posta kutusuna erişim ayrı özelliklerdir.
Çoklu hesap kaydı: provider, doğrulanmış hesap kimliği, granted scopes, şifreli
token, expiry; token anahtarı repo dışında/OS güvenli saklama alanında tutulur.

Alıcı adresi doğrulanmış ilandan gelir; konu/ek/belge hash'i pakete bağlanır.
Microsoft 202 kabul cevabı teslim edildi anlamına gelmez. Gönderildi, işverene
ulaştı ve başvuru alındı ayrı kanıtlardır. Gelen e-posta düşük güvenle eşleşirse
durum otomatik değiştirilmez. Otomatik takip e-postası ayrı izin gerektirir.

UI: onboarding → önerilen meslekler → otomasyon ayarları → çalışma merkezi.
Merkezde çalışan/duran motor, neden beklediği, soru gelen kutusu, hazırlanmış
paket, kaynak sağlığı, günlük limit ve acil durdurma görünür. Yanlış sabit
"Dry Run is on" metni yerine gerçek sunucu durumu kullanılır.

Güvenlik: localhost varsayılanı; CSRF ve oturum sınırları; dış URL/redirect için
SSRF ve domain doğrulama; belgelerde rastgele storage ID + boyut/tür kontrolü;
CV/site/e-posta içindeki talimatlar güvenilmez veri; yalnız gerekli bilgiler
modele gönderilir. Log ve hata mesajlarında token/kişisel bilgi bulunmaz.
Yedekleme, dışa aktarma, silme ve saklama süresi UI'da açık olmalıdır.

## 9. Aşamalı tasarım ve teslim planı

Her aşama: küçük teknik spec → başarısız test → uygulama → test/inceleme →
sentetik senaryo → özellik bayrağı. Canlı gönderim yalnız uygun izinle yapılır.

| Sıra | Teslim | Ana dosya alanları | Çıkış ölçütü |
|---|---|---|---|
| P0 | Veri ve güvenlik temeli | db/migrations, candidate/models, app/onboarding | Mevcut DB kayıpsız yükselir; rollback/yedek testi; taslak otomatik gerçek olmaz |
| P1 | Profil onayı ve cevap çözümleyici | candidate/confirmation, applications/resolver | Bilinmeyen, çelişkili, süresi geçmiş ve yanlış ülke cevapları ask/block verir |
| P2 | CV ve meslekler | candidate/service, careers/taxonomy, cv/service | FI/EN örneklerde kaynaklı çıkarım; meslekler kullanıcı seçmeden hedef olmaz |
| P3 | Kalıcı arama/iş motoru | automation/worker, jobs/runner, jobs/service | Restart devamı, kaynak adaleti, süresi dolan ilan ve eşzamanlı duplicate testleri |
| P4 | Başvuru paketi ve yarı otomatik ATS | applications/packages, agents/forms, agents/adapters | Çok adım/upload/dinamik soru; değişen paket onayı bozar; kanıt yoksa başarı yok |
| P5 | Gönderim kanalları | integrations/email_oauth, integrations/outbox | Scope reddi, token expiry, yanlış hesap, timeout ve duplicate senaryoları güvenli |
| P6 | Sınırlı gözetimsiz pilot | automation/policy, app/automation, skill/docs | İzin iptali/kota/kill switch her gönderimde çalışır; yalnız doğrulanmış adaptörler |
| P7 | Takip ve dağıtım | integrations/mailbox, analytics, backup/export | Yanlış mail eşleşmesi durumu bozmaz; kişisel veri içermeyen dağıtım paketi |

P1 başlamadan önce P0 veri sözleşmesi sabitlenir. P4 gerçek gönderimi etkinleştirmeden
önce profil ve politika kontrolleri tamamlanır. P6, P4/P5'in canlı entegrasyon
kanıtları olmadan tamamlandı sayılmaz. Tek devasa PR yerine aşama bazlı teslim yapılır.

## 10. Doğrulama ve sürüm kabulü

Unit: durum geçişleri, kapsam, tarih, kota, izin, kaynaklı cevap.
Integration: migration, transaction/lease, outbox, hash invalidation, OAuth mocks.
E2E: boş profil → taslak → onay → hedef → sentetik ilan → form → kanıt.
Chaos: gönderim anında process crash, ağ kesilmesi, token iptali, aynı işte iki worker.
Security: kötü niyetli ilan/CV, unsafe redirect, sahte dosya türü, log redaction.

Gözetimsiz pilot için: en az 20 farklı sentetik form senaryosu, desteklenen her
adaptörde denetimli uçtan uca doğrulama, testlerde sıfır yetkisiz gönderim ve sıfır
mükerrer gönderim; bilinmeyen sonuçların tamamı durdurulup uzlaştırılmalı. Bu ölçütler
tüm sitelerde başarı garantisi değildir. Metrikler: doğrulanmış gönderim, manuel
müdahale sebebi, eksik bilgi oranı, kaynak güncelliği ve maliyet; yalnız başvuru sayısı değil.

## 11. Açık ürün kararı

Önerilen başlangıç yerel tek aday kurulumu, FI/EN ve yarı otomatik doğrulanmış pilot.
Herkes kendi kurulumunu kullanabilir. Kullanıcı hesabı/çok kiracılı bulut istenirse
tenant izolasyonu ve hosted OAuth dağıtımı ayrı mimari tasarım gerektirir.
Gözetimsiz gönderim yetkisinin kapsamı, mevcut özel onay kuralının nasıl değişeceği
ile birlikte yazılı spec incelemesinde kararlaştırılmalıdır.

## Resmi entegrasyon kaynakları

- ESCO API: https://esco.ec.europa.eu/en/use-esco/use-esco-services-api/esco-web-service-api
- Gmail scopes: https://developers.google.com/workspace/gmail/api/auth/scopes
- Microsoft sendMail ve 202 semantiği: https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0

Bu dosya önceki kod incelemesinin tarihsel kaydıdır. Güncel, doğrulanmış durum;
yetki ayrımı ve uygulanabilir aşama planı için `../superpowers/specs/2026-09-29-full-application-automation-design.md`
ve `../superpowers/plans/2026-09-29-full-application-automation.md` belgelerine bakın.

## 2026-09-29 uygulama durumu eki

Önceki bölümlerde “henüz uygulanmadı” olarak yazılan bazı maddeler bu çalışma
alanında artık uygulanmıştır: fail-closed Playwright adaptörü, görünür kalıcı
Chromium oturumu, ilan doğrulama yaşı ve kullanıcı incelemesi, yerel worker
lease'i, CV'nin başvuru klasörüne kopyalanması, kaynak cursor'ı, 63 soruluk
onboarding, exact-package Smart Approval ve gönderim sonucu kanıtı. Smart Approval
tam cevap/CV checksum paketini kullanıcıya gösterir; aynı paket hash'i onaylanmadan
aday verisi forma yazılmaz. Submit öncesi form imzası, origin, gerçek alan değerleri
ve seçilen CV checksum'ı da yeniden kontrol edilir. İlan doğrulaması 24 saatte
sona erer ve snapshot hash'i başlık, işveren, lokasyon, dil, açıklama, hedef URL,
son tarih veya kaynak değişikliğini algılar; UI yeniden inceleme ister.

Bu turda `py -m pytest -q` sonucu 218 passed olmuştur. Bir Starlette/httpx
deprecation warning vardır. Bu testler gerçek OAuth sağlayıcıları, KIPA erişimi,
canlı ATS sayfaları, e-posta gönderimi veya Autopilot'ın tüm sitelerde çalıştığını
kanıtlamaz. Gmail/Outlook bağlantısı salt-okumadır; e-posta ile başvuru gönderimi
henüz uygulanmamıştır. Taksonomi yalnızca beş sabit meslek içerir ve gerçek ATS
adaptörlerinin staging doğrulaması bekler.
