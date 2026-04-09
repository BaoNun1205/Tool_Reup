# Product Overlay Logic

## 1. Mục tiêu file

Tài liệu này mô tả bố cục chèn ảnh sản phẩm, cách scale ảnh, cách xử lý PNG và JPG, hiệu ứng hòa mép chính cho MVP và các fallback thị giác cần có. Mục tiêu là tạo một hướng render ổn định, dễ build và đủ sạch để dùng thực tế.

## 2. Nội dung chính

### Ảnh sản phẩm được đặt ở đâu

MVP đặt ảnh sản phẩm vào `góc trên-trái` của canvas final, có margin an toàn với mép ngoài. Lý do chọn vị trí này là để hạn chế đụng vùng UI tự nhiên của TikTok ở cạnh phải và vùng caption thường nằm thấp phía dưới sau khi video được đăng lại lên nền tảng.

Overlay không được chạm sát mép. Cần có safe margin đủ để sản phẩm nhìn như một thành phần có chủ đích, không giống bị dán vào góc.

### Bố cục chính của video final

Bố cục final cho MVP:

- Video nguồn là lớp nền full-frame của canvas portrait.
- Ảnh sản phẩm là lớp overlay tĩnh xuyên suốt video.
- Không có animation di chuyển ảnh trong MVP.
- Không có nhiều ảnh hoặc thay đổi vị trí theo scene trong MVP.

Mục tiêu của bố cục là đơn giản, ít rủi ro và đủ nhất quán để builder triển khai nhanh.

### Logic scale ảnh

Quy tắc scale cho MVP:

- Giữ nguyên tỉ lệ ảnh, không bóp méo.
- Ảnh nên chiếm khoảng `24% đến 30%` chiều rộng canvas, tùy aspect ratio ảnh.
- Đồng thời giới hạn chiều cao overlay để không chiếm quá nhiều vùng xem chính.
- Nếu ảnh nguồn quá nhỏ, không phóng lớn quá mức gây bể hình; ưu tiên giữ nhỏ hơn và dùng nền hỗ trợ mềm.

Mục tiêu là để sản phẩm đủ nổi bật nhưng không biến phần nền video thành thứ yếu.

### Cách xử lý PNG nền trong suốt và JPG thường

#### PNG nền trong suốt

- Dùng alpha gốc của ảnh làm nền tảng compositing.
- Làm mềm viền alpha ở mức nhẹ để tránh cảm giác cắt sắc hoặc viền alias.
- Thêm một lớp shadow hoặc glow nhẹ phía sau để sản phẩm tách khỏi nền video.

#### JPG hoặc ảnh không có alpha

- Không cố tách nền tự động ở MVP.
- Đặt ảnh trong một `soft product panel` có bo góc nhẹ, viền mềm và nền đỡ bán trong suốt.
- Panel này giúp che bớt cảm giác ảnh bị dán cứng vào video và làm cho JPG thường nhìn có chủ đích hơn.

### Cách tạo hiệu ứng “mờ mờ / mềm mép / hòa giữa ảnh và video”

Phương án hiệu ứng chính cho MVP là:

- Dùng `feathered edge` quanh biên overlay.
- Kết hợp `soft shadow hoặc glow nhẹ` phía sau sản phẩm.
- Với JPG, thêm một lớp panel mềm có opacity thấp và mép bo mềm.

Hiệu ứng này đủ gần với yêu cầu “hòa mép” nhưng vẫn dễ render, ổn định và ít tạo artifact hơn các kỹ thuật cắt nền hay blend quá phức tạp.

### Chọn 1 phương án hiệu ứng chính cho MVP

Phương án chính được chốt cho MVP là:

- `PNG`: alpha gốc + feather nhẹ + soft shadow/glow.
- `JPG`: soft product panel + feather biên + soft shadow.

Không dùng animation nổi, không dùng mask động, không dùng background removal tự động.

### Phương án fallback khi ảnh sản phẩm không đẹp hoặc không có nền trong suốt

Fallback của MVP:

- Giảm kích thước overlay để bớt lộ nhược điểm ảnh.
- Dùng soft panel phía sau để tạo cảm giác bố cục có chủ đích.
- Ghi warning trong metadata rằng ảnh không phải dạng alpha tối ưu.

Nếu ảnh quá nhỏ hoặc quá xấu, hệ thống vẫn có thể render nhưng phải cảnh báo chất lượng thay vì cố “sửa thông minh”.

### Các lỗi thị giác thường gặp

- Overlay quá to, che mất nội dung chính.
- Ảnh bị stretch vì scale sai tỉ lệ.
- PNG có viền trắng hoặc viền gãy do alpha không sạch.
- JPG nền rối nhìn như dán ảnh thô nếu thiếu panel hỗ trợ.
- Đặt sai safe zone khiến khi đăng lên TikTok, overlay bị UI nền tảng che.

## 3. Quyết định thiết kế chính

- Chọn `góc trên-trái` làm vị trí overlay mặc định cho MVP.
- Chọn `overlay tĩnh xuyên suốt video` thay vì animation hay scene-aware placement.
- Chọn `feathered edge + soft shadow/glow` làm hiệu ứng hòa mép chính.
- Chọn `soft product panel` làm fallback cho JPG thường.
- Không giải bài toán background removal thông minh trong MVP.

## 4. Điểm khó / rủi ro / chỗ dễ fail khi build

- Vị trí overlay cố định không thể tránh mọi trường hợp che nội dung quan trọng vì MVP chưa có scene understanding.
- Feather quá mạnh sẽ làm ảnh bị mờ và mất độ sắc của sản phẩm.
- Feather quá nhẹ hoặc shadow quá gắt sẽ tạo cảm giác dán ảnh.
- JPG nền xấu là rủi ro thẩm mỹ lớn nhất của MVP vì hệ thống không cắt nền tự động.

## 5. Tiêu chí hoàn thành

- File này phải chốt rõ vị trí, bố cục và nguyên tắc scale ảnh.
- Phải có hướng riêng cho PNG alpha và JPG thường.
- Phải chọn dứt khoát một hiệu ứng hòa mép chính cho MVP.
- Builder AI đọc file này phải có thể build product overlay ổn định mà không cần suy diễn thêm.
