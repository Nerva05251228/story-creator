from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Text, Boolean, Float
from sqlalchemy.orm import relationship
from sqlalchemy import Numeric
from datetime import datetime
from database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    token = Column(String, unique=True, index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    sora_rule = Column(String, default="准则：不要出现字幕")  # Sora提示词准则
    password_hash = Column(String, default="")  # 密码哈希（sha256）
    password_plain = Column(String, default="123456")  # 明文密码（管理端展示）

    # 鍏崇郴
    story_libraries = relationship("StoryLibrary", back_populates="owner", cascade="all, delete-orphan")
    scripts = relationship("Script", back_populates="owner", cascade="all, delete-orphan")

class StoryLibrary(Base):
    __tablename__ = "story_libraries"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    episode_id = Column(Integer, ForeignKey("episodes.id"), nullable=True)  # 鍏宠仈鍒板墽闆?
    name = Column(String, nullable=False)
    description = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    # 鍏崇郴
    owner = relationship("User", back_populates="story_libraries")
    episode = relationship("Episode", back_populates="library")
    subject_cards = relationship("SubjectCard", back_populates="library", cascade="all, delete-orphan")

class SubjectCard(Base):
    __tablename__ = "subject_cards"

    id = Column(Integer, primary_key=True, index=True)
    library_id = Column(Integer, ForeignKey("story_libraries.id"), nullable=False)
    name = Column(String, nullable=False)
    alias = Column(String, default="")
    card_type = Column(String, nullable=False)  # 瑙掕壊/鍦烘櫙
    linked_card_id = Column(Integer, nullable=True, index=True)  # 声音卡片绑定的角色卡片ID
    ai_prompt = Column(Text, default="")  # 澶栬矊/鍦烘櫙鎻忚堪锛堜笉鍚鏍硷級
    ai_prompt_status = Column(String, nullable=True, default=None)  # 旧字段：主体AI提示词生成状态
    role_personality = Column(String, default="")  # 角色性格（中文一句话）
    style_template_id = Column(Integer, ForeignKey("style_templates.id"), nullable=True)  # 鍏宠仈椋庢牸妯℃澘
    is_protagonist = Column(Boolean, default=False)  # 鏄惁涓轰富瑙掞紙浠呰鑹诧級
    protagonist_gender = Column(String, default="")  # male/female
    is_generating_images = Column(Boolean, default=False)  # 鏄惁姝ｅ湪鐢熸垚鍥剧墖
    generating_count = Column(Integer, default=0)  # 姝ｅ湪鐢熸垚鐨勫浘鐗囨暟閲?
    created_at = Column(DateTime, default=datetime.utcnow)

    # 鍏崇郴
    library = relationship("StoryLibrary", back_populates="subject_cards")
    images = relationship("CardImage", back_populates="card", cascade="all, delete-orphan")
    audios = relationship("SubjectCardAudio", back_populates="card", cascade="all, delete-orphan")
    generated_images = relationship("GeneratedImage", back_populates="card", cascade="all, delete-orphan")
    style_template = relationship("StyleTemplate")

class CardImage(Base):
    __tablename__ = "card_images"

    id = Column(Integer, primary_key=True, index=True)
    card_id = Column(Integer, ForeignKey("subject_cards.id"), nullable=False)
    image_path = Column(String, nullable=False)
    order = Column(Integer, default=0)  # 鍥剧墖椤哄簭
    created_at = Column(DateTime, default=datetime.utcnow)

    # 鍏崇郴
    card = relationship("SubjectCard", back_populates="images")


class SubjectCardAudio(Base):
    __tablename__ = "subject_card_audios"

    id = Column(Integer, primary_key=True, index=True)
    card_id = Column(Integer, ForeignKey("subject_cards.id"), nullable=False, index=True)
    audio_path = Column(String, nullable=False)
    file_name = Column(String, default="")
    duration_seconds = Column(Float, default=0.0)
    is_reference = Column(Boolean, default=False)  # 当前素材
    created_at = Column(DateTime, default=datetime.utcnow)

    # 鍏崇郴
    card = relationship("SubjectCard", back_populates="audios")

# 鏂板锛氬墽鏈〃
class Script(Base):
    __tablename__ = "scripts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)  # 鍓у悕
    sora_prompt_style = Column(Text, default="")  # Sora鍒嗛暅鎻愮ず璇嶇敓鎴愯鍒?
    video_prompt_template = Column(Text, default="")  # 故事板Sora最终视频提示词模板（剧本级覆盖）
    style_template = Column(Text, default="")  # 缁樺浘椋庢牸妯℃澘鍐呭
    narration_template = Column(Text, default="")  # 鏂囨湰杞В璇村墽鎻愮ず璇嶆ā鏉?
    voiceover_shared_data = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    # 鍏崇郴
    owner = relationship("User", back_populates="scripts")
    episodes = relationship("Episode", back_populates="script", cascade="all, delete-orphan")

# 鏂板锛氱墖娈佃〃
class Episode(Base):
    __tablename__ = "episodes"

    id = Column(Integer, primary_key=True, index=True)
    script_id = Column(Integer, ForeignKey("scripts.id"), nullable=False)
    name = Column(String, nullable=False)  # 鐗囨鍚嶏紙濡係01锛?
    content = Column(Text, default="")  # 鏂囨
    batch_size = Column(Integer, default=500)  # 鍒嗘壒瀛楁暟
    simple_storyboard_data = Column(Text, default="")  # 绠€鍗曞垎闀淛SON鏁版嵁
    simple_storyboard_generating = Column(Boolean, default=False)  # 鏄惁姝ｅ湪鐢熸垚绠€鍗曞垎闀?
    simple_storyboard_error = Column(Text, default="")  # 绠€鍗曞垎闀滅敓鎴愰敊璇俊鎭?
    storyboard_data = Column(Text, default="")  # 璇︾粏鍒嗛暅琛↗SON鏁版嵁
    storyboard_generating = Column(Boolean, default=False)  # 鏄惁姝ｅ湪鐢熸垚璇︾粏鍒嗛暅琛?
    storyboard_error = Column(Text, default="")  # 璇︾粏鍒嗛暅琛ㄧ敓鎴愰敊璇俊鎭?
    voiceover_data = Column(Text, default="")  # 閰嶉煶琛ㄦ暟鎹紙绾噣鐨剉oice_type/narration/dialogue锛?
    batch_generating_prompts = Column(Boolean, default=False)  # 鏄惁姝ｅ湪鎵归噺鐢熸垚鎻愮ず璇?
    batch_generating_storyboard2_prompts = Column(Boolean, default=False)  # 鏄惁姝ｅ湪鎵归噺鐢熸垚鏁呬簨鏉?鎻愮ず璇?
    narration_converting = Column(Boolean, default=False)  # 鏄惁姝ｅ湪杞崲涓鸿В璇村墽
    narration_error = Column(Text, default="")  # 杞崲涓鸿В璇村墽鐨勯敊璇俊鎭?
    opening_content = Column(Text, default="")  # 绮惧僵寮€澶村唴瀹?
    opening_generating = Column(Boolean, default=False)  # 鏄惁姝ｅ湪鐢熸垚绮惧僵寮€澶?
    opening_error = Column(Text, default="")  # 绮惧僵寮€澶寸敓鎴愰敊璇俊鎭?
    shot_image_size = Column(String, default="9:16")  # 闀滃ご鍥剧粺涓€灏哄璁剧疆锛堣窡闅忓墽闆嗭級
    detail_images_model = Column(String, default="seedream-4.0")  # 镜头图模型：上游作图模型key
    detail_images_provider = Column(String, default="")  # 镜头图服务商：由上游模型目录动态决定
    storyboard2_video_duration = Column(Integer, default=6)  # 鏁呬簨鏉?瑙嗛榛樿鏃堕暱锛?/10绉掞級
    storyboard2_image_cw = Column(Integer, default=50)  # 即梦生图参考强度（cw）
    storyboard2_duration = Column(Integer, default=15)  # 简单分镜时长规格：15/25/35(规则分段)
    storyboard2_include_scene_references = Column(Boolean, default=False)  # 鏁呬簨鏉?鏄惁鎼哄甫鍦烘櫙涓讳綋鍙傝€冨浘
    storyboard_video_model = Column(String, default="Seedance 2.0 VIP")  # 鏁呬簨鏉匡紙sora锛夋ā鍨?
    storyboard_video_aspect_ratio = Column(String, default="16:9")  # 鏁呬簨鏉匡紙sora锛夎棰戞瘮渚?
    storyboard_video_duration = Column(Integer, default=15)  # 鏁呬簨鏉匡紙sora锛夎棰戞椂闀匡紙绉掞級
    storyboard_video_resolution_name = Column(String, default="720p")
    storyboard_video_appoint_account = Column(String, default="")
    video_style_template_id = Column(Integer, nullable=True)  # 瑙嗛椋庢牸妯℃澘ID
    video_prompt_template = Column(Text, default="")  # 故事板Sora最终视频提示词模板（剧集级覆盖）
    billing_version = Column(Integer, default=1, nullable=False)  # 仅新剧集接入新计费体系
    created_at = Column(DateTime, default=datetime.utcnow)

    # 鍏崇郴
    script = relationship("Script", back_populates="episodes")
    library = relationship("StoryLibrary", back_populates="episode", uselist=False)  # 涓€瀵逛竴
    shots = relationship("StoryboardShot", back_populates="episode", cascade="all, delete-orphan")
    storyboard2_shots = relationship("Storyboard2Shot", back_populates="episode", cascade="all, delete-orphan")


class SimpleStoryboardBatch(Base):
    __tablename__ = "simple_storyboard_batches"

    id = Column(Integer, primary_key=True, index=True)
    episode_id = Column(Integer, ForeignKey("episodes.id"), nullable=False, index=True)
    batch_index = Column(Integer, nullable=False, index=True)
    total_batches = Column(Integer, default=0, nullable=False)
    status = Column(String, default="pending", nullable=False)  # pending/submitting/completed/failed
    source_text = Column(Text, default="")
    shots_data = Column(Text, default="")
    error_message = Column(Text, default="")
    last_attempt = Column(Integer, default=0, nullable=False)
    retry_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# 鏂板锛氭晠浜嬫澘闀滃ご琛?
class StoryboardShot(Base):
    __tablename__ = "storyboard_shots"

    id = Column(Integer, primary_key=True, index=True)
    episode_id = Column(Integer, ForeignKey("episodes.id"), nullable=False)
    shot_number = Column(Integer, nullable=False)  # 闀滃ご搴忓彿
    stable_id = Column(String(36), nullable=True, index=True)  # 绋冲畾ID锛圲UID锛夛紝鐢ㄤ簬璺熻釜闀滃ご韬唤
    variant_index = Column(Integer, default=0)  # 闀滃ご鍙樹綋搴忓彿锛?涓哄師濮嬶級
    prompt_template = Column(Text, default="")  # 鎻愮ず璇?锛堟ā鏉匡級
    script_excerpt = Column(Text, default="")  # 鍘熷墽鏈钀斤紙瀵煎叆鍒嗛暅琛級
    storyboard_video_prompt = Column(Text, default="")  # 鍒嗛暅瑙嗛鎻愮ず璇?
    storyboard_audio_prompt = Column(Text, default="")  # 鍒嗛暅闊抽鎻愮ず璇?
    storyboard_dialogue = Column(Text, default="")  # 鍒嗛暅鍙拌瘝
    scene_override = Column(Text, default="")  # 鍦烘櫙鎻忚堪锛堢敤鎴峰彲缂栬緫锛岀嫭绔嬩簬涓讳綋鍗＄墖锛?
    scene_override_locked = Column(Boolean, default=False, nullable=False)  # 鍦烘櫙鎻忚堪鏄惁閿佸畾锛堜笉鍐嶈嚜鍔ㄥ～鍏咃級
    sora_prompt = Column(Text, default="")  # 鍚堝苟鍚庣殑Sora鎻愮ず璇嶏紙鍒嗛暅琛ㄦ牸閮ㄥ垎锛?
    sora_prompt_is_full = Column(Boolean, default=False, nullable=False)  # 是否为一次性完整提示词（不再二次拼接）
    sora_prompt_status = Column(String, default="idle")  # idle/generating/completed/failed
    reasoning_prompt_status = Column(String, default="idle")  # idle/generating/completed/failed
    selected_card_ids = Column(String, default="[]")  # JSON鏁扮粍锛歔1,2,3]
    selected_sound_card_ids = Column(String, nullable=True)  # JSON数组；NULL 表示按默认规则自动选择声音卡片
    video_path = Column(String, default="")  # ????????URL
    thumbnail_video_path = Column(String, default="")  # 鍗＄墖缂╃暐瑙嗛璺緞
    video_status = Column(String, default="idle")  # idle/processing/completed/failed
    task_id = Column(String, default="")  # Sora??ID
    aspect_ratio = Column(String, default="16:9")  # 瑙嗛姣斾緥锛?:16鎴?6:9
    duration = Column(Integer, default=15)  # 瑙嗛鏃堕暱锛?0鎴?5绉?
    storyboard_video_model = Column(String, default="")  # 镜头当前/覆盖的视频模型
    storyboard_video_appoint_account = Column(String, default="")  # 镜头当前/覆盖的视频账号（空表示跟随全局）
    storyboard_video_model_override_enabled = Column(Boolean, default=False, nullable=False)  # 是否单独覆盖镜头模型
    duration_override_enabled = Column(Boolean, default=False, nullable=False)  # 是否单独覆盖镜头时长
    provider = Column(String, default="yijia")  # 瑙嗛鏈嶅姟鍟嗭細apimart/suchuang/yijia
    cdn_uploaded = Column(Boolean, default=False)  # CDN鏄惁宸蹭笂浼?
    video_submitted_at = Column(DateTime, nullable=True)  # 瑙嗛鎻愪氦鏃堕棿
    video_error_message = Column(Text, default="")  # 瑙嗛鐢熸垚閿欒淇℃伅
    price = Column(Integer, default=0)  # 瑙嗛鐢熸垚浠锋牸锛堝崟浣嶏細鍒嗭紝濡?0琛ㄧず0.8鍏冿級
    timeline_json = Column(Text, default="")  # AI生成的原始timeline JSON数据
    detail_image_prompt_overrides = Column(Text, default="{}")  # 镜头图文案覆盖（按子镜头序号存储）
    storyboard_image_path = Column(String, default="")  # 鍒嗛暅鍥綜DN URL
    storyboard_image_status = Column(String, default="idle")  # idle/processing/completed/failed
    storyboard_image_task_id = Column(String, default="")  # 鍒嗛暅鍥句换鍔D
    storyboard_image_model = Column(String, default="")  # 镜头图使用的作图模型：banana2 / banana2-moti / banana-pro
    first_frame_reference_image_url = Column(String, default="")  # 视频首帧参考图 URL（为空表示未选择）
    uploaded_first_frame_reference_image_url = Column(String, default="")  # 本地上传的首帧候选图 URL（仅加入候选，不自动选中）
    uploaded_scene_image_url = Column(String, default="")  # 镜头级上传的场景图片 URL
    use_uploaded_scene_image = Column(Boolean, default=False, nullable=False)  # 是否用镜头级上传场景图替代场景卡图片
    created_at = Column(DateTime, default=datetime.utcnow)

    # 鍏崇郴
    episode = relationship("Episode", back_populates="shots")
    videos = relationship("ShotVideo", back_populates="shot", cascade="all, delete-orphan")
    collages = relationship("ShotCollage", back_populates="shot", cascade="all, delete-orphan")
    detail_images = relationship("ShotDetailImage", back_populates="shot", cascade="all, delete-orphan")

# 鏂板锛氶暅澶寸粏鍖栧浘鐗囪〃锛堝瓙闀滃ご鍥剧墖锛?
class ShotDetailImage(Base):
    __tablename__ = "shot_detail_images"

    id = Column(Integer, primary_key=True, index=True)
    shot_id = Column(Integer, ForeignKey("storyboard_shots.id"), nullable=False)
    sub_shot_index = Column(Integer, nullable=False)  # 瀛愰暅澶村簭鍙凤紙1,2,3...锛?
    time_range = Column(String, default="")  # 鏃堕棿鑼冨洿锛堝"00s-04s"锛?
    visual_text = Column(Text, default="")  # 鐢婚潰鎻忚堪鍘熸枃
    audio_text = Column(Text, default="")  # 鍙拌瘝闊虫晥鍘熸枃
    optimized_prompt = Column(Text, default="")  # CC浼樺寲鍚庣殑鎻愮ず璇?
    images_json = Column(Text, default="[]")  # JSON鏁扮粍瀛樺偍鍥剧墖URL
    status = Column(String, default="idle")  # idle/processing/completed/failed
    error_message = Column(Text, default="")  # 閿欒淇℃伅
    task_id = Column(String, default="", index=True)  # 外部生图任务ID
    provider = Column(String, default="")  # 任务提供商
    model_name = Column(String, default="")  # 规范模型键
    submit_api_url = Column(Text, default="")  # 提交接口
    status_api_url = Column(Text, default="")  # 查询接口
    query_error_count = Column(Integer, default=0)  # 连续查询异常次数
    last_query_error = Column(Text, default="")  # 最近一次查询异常
    submitted_at = Column(DateTime, nullable=True)  # 提交时间
    last_query_at = Column(DateTime, nullable=True)  # 最近一次查询时间
    created_at = Column(DateTime, default=datetime.utcnow)

    # 鍏崇郴
    shot = relationship("StoryboardShot", back_populates="detail_images")

# 鏂板锛氭晠浜嬫澘2闀滃ご琛紙鐙珛浜巗toryboard_shots锛?
class Storyboard2Shot(Base):
    __tablename__ = "storyboard2_shots"

    id = Column(Integer, primary_key=True, index=True)
    episode_id = Column(Integer, ForeignKey("episodes.id"), nullable=False, index=True)
    source_shot_id = Column(Integer, ForeignKey("storyboard_shots.id"), nullable=True)
    shot_number = Column(Integer, nullable=False)
    excerpt = Column(Text, default="")
    selected_card_ids = Column(String, default="[]")  # JSON鏁扮粍锛歔1,2,3]
    display_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    # 鍏崇郴
    episode = relationship("Episode", back_populates="storyboard2_shots")
    source_shot = relationship("StoryboardShot")
    sub_shots = relationship("Storyboard2SubShot", back_populates="shot", cascade="all, delete-orphan")

# 鏂板锛氭晠浜嬫澘2鍒嗛暅琛?
class Storyboard2SubShot(Base):
    __tablename__ = "storyboard2_subshots"

    id = Column(Integer, primary_key=True, index=True)
    storyboard2_shot_id = Column(Integer, ForeignKey("storyboard2_shots.id"), nullable=False, index=True)
    sub_shot_index = Column(Integer, nullable=False)  # 鍒嗛暅搴忓彿锛?,2,3...锛?
    time_range = Column(String, default="")
    visual_text = Column(Text, default="")
    audio_text = Column(Text, default="")
    sora_prompt = Column(Text, default="")
    scene_override = Column(Text, default="")  # 场景描述（分镜级，可手动编辑）
    scene_override_locked = Column(Boolean, default=False, nullable=False)  # 手动编辑后锁定，不再自动提取
    selected_card_ids = Column(String, default="[]")  # JSON鏁扮粍锛屽垎闀滅骇涓讳綋缁戝畾
    image_generate_status = Column(String, default="idle")  # idle/processing/failed
    image_generate_progress = Column(String, default="")  # 鐢熸垚杩涘害锛屽 "1/4"
    image_generate_error = Column(Text, default="")  # 鐢熸垚閿欒淇℃伅
    current_image_id = Column(Integer, nullable=True, index=True)  # 鍏佽寮曠敤浠绘剰鍒嗛暅鍊欓€夊浘
    created_at = Column(DateTime, default=datetime.utcnow)

    # 鍏崇郴
    shot = relationship("Storyboard2Shot", back_populates="sub_shots")
    images = relationship("Storyboard2SubShotImage", back_populates="sub_shot", cascade="all, delete-orphan")
    videos = relationship("Storyboard2SubShotVideo", back_populates="sub_shot", cascade="all, delete-orphan")

# 鏂板锛氭晠浜嬫澘2鍊欓€夊浘琛?
class Storyboard2SubShotImage(Base):
    __tablename__ = "storyboard2_subshot_images"

    id = Column(Integer, primary_key=True, index=True)
    sub_shot_id = Column(Integer, ForeignKey("storyboard2_subshots.id"), nullable=False, index=True)
    image_url = Column(String, nullable=False)
    size = Column(String, default="9:16")
    created_at = Column(DateTime, default=datetime.utcnow)

    # 鍏崇郴
    sub_shot = relationship("Storyboard2SubShot", back_populates="images")


class Storyboard2SubShotVideo(Base):
    __tablename__ = "storyboard2_subshot_videos"

    id = Column(Integer, primary_key=True, index=True)
    sub_shot_id = Column(Integer, ForeignKey("storyboard2_subshots.id"), nullable=False, index=True)
    task_id = Column(String, default="", index=True)
    model_name = Column(String, default="grok")
    duration = Column(Integer, default=10)
    aspect_ratio = Column(String, default="9:16")
    video_url = Column(String, default="")
    thumbnail_url = Column(String, default="")
    status = Column(String, default="processing")  # submitted/pending/processing/completed/failed
    progress = Column(Integer, default=0)
    error_message = Column(Text, default="")
    cdn_uploaded = Column(Boolean, default=False)
    is_deleted = Column(Boolean, default=False)
    deleted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # 鍏崇郴
    sub_shot = relationship("Storyboard2SubShot", back_populates="videos")

# 鏂板锛氬叏灞€鎻愮ず璇嶆ā鏉胯〃
class PromptTemplate(Base):
    __tablename__ = "prompt_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)  # 妯℃澘鍚嶇О锛堝"2D鍔ㄧ敾"锛?
    content = Column(Text, nullable=False)  # 妯℃澘鍐呭
    is_default = Column(Boolean, default=False)  # 鏄惁榛樿妯℃澘
    created_at = Column(DateTime, default=datetime.utcnow)


class LargeShotTemplate(Base):
    __tablename__ = "large_shot_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    is_default = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class StoryboardSoraPromptTemplate(Base):
    __tablename__ = "storyboard_sora_prompt_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    is_default = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class DashboardTaskLog(Base):
    __tablename__ = "dashboard_task_logs"

    id = Column(Integer, primary_key=True, index=True)
    task_key = Column(String, unique=True, index=True, nullable=False)
    task_folder = Column(String, index=True, default="")
    source_type = Column(String, index=True, default="debug")
    source_record_type = Column(String, index=True, default="")
    source_record_id = Column(Integer, nullable=True, index=True)
    task_type = Column(String, index=True, default="")
    stage = Column(String, default="")
    title = Column(String, default="")
    status = Column(String, index=True, default="submitting")
    creator_user_id = Column(Integer, nullable=True, index=True)
    creator_username = Column(String, index=True, default="")
    script_id = Column(Integer, nullable=True, index=True)
    script_name = Column(String, default="")
    episode_id = Column(Integer, nullable=True, index=True)
    episode_name = Column(String, default="")
    shot_id = Column(Integer, nullable=True, index=True)
    shot_number = Column(Integer, nullable=True)
    batch_id = Column(String, default="")
    provider = Column(String, default="")
    model_name = Column(String, default="")
    api_url = Column(Text, default="")
    status_api_url = Column(Text, default="")
    external_task_id = Column(String, default="")
    input_payload = Column(Text, default="")
    output_payload = Column(Text, default="")
    raw_response_payload = Column(Text, default="")
    result_payload = Column(Text, default="")
    error_message = Column(Text, default="")
    result_summary = Column(Text, default="")
    latest_filename = Column(String, default="")
    latest_event_payload = Column(Text, default="")
    events_json = Column(Text, default="[]")
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)


class AnalyzeTemplate(Base):
    __tablename__ = "analyze_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    prompt_style = Column(Text, nullable=False)
    is_default = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

# 鏂板锛欰I鐢熸垚鍥剧墖琛?
class GeneratedImage(Base):
    __tablename__ = "generated_images"

    id = Column(Integer, primary_key=True, index=True)
    card_id = Column(Integer, ForeignKey("subject_cards.id"), nullable=False)
    image_path = Column(String, nullable=False)  # CDN URL
    model_name = Column(String, nullable=False)  # seedream-4-5 / banana2 / banana2-moti / banana-pro
    is_reference = Column(Boolean, default=False)  # 鏄惁浣滀负鍙傝€冨浘
    task_id = Column(String, default="")  # Sora??ID
    status = Column(String, default="processing")  # processing/completed/failed
    created_at = Column(DateTime, default=datetime.utcnow)

    # 鍏崇郴
    card = relationship("SubjectCard", back_populates="generated_images")

# 鏂板锛氶暅澶磋棰戣褰曡〃
class ShotVideo(Base):
    __tablename__ = "shot_videos"

    id = Column(Integer, primary_key=True, index=True)
    shot_id = Column(Integer, ForeignKey("storyboard_shots.id"), nullable=False)
    video_path = Column(String, nullable=False)  # CDN URL
    created_at = Column(DateTime, default=datetime.utcnow)

    # 鍏崇郴
    shot = relationship("StoryboardShot", back_populates="videos")

# 鏂板锛氶暅澶存嫾鍥捐褰曡〃
class ShotCollage(Base):
    __tablename__ = "shot_collages"

    id = Column(Integer, primary_key=True, index=True)
    shot_id = Column(Integer, ForeignKey("storyboard_shots.id"), nullable=False)
    collage_path = Column(String, nullable=False)  # CDN URL
    is_selected = Column(Boolean, default=False)  # 鏄惁琚€変腑浣滀负鍙傝€冨浘
    card_ids_hash = Column(String, nullable=True)  # 涓讳綋ID缁勫悎鐨勫搱甯屽€硷紝鐢ㄤ簬鎷煎浘澶嶇敤
    created_at = Column(DateTime, default=datetime.utcnow)

    # 鍏崇郴
    shot = relationship("StoryboardShot", back_populates="collages")

# 鏂板锛氭彁绀鸿瘝绠＄悊琛?
class PromptConfig(Base):
    __tablename__ = "prompt_configs"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, nullable=False)  # 鍞竴鏍囪瘑绗?
    name = Column(String, nullable=False)  # 鎻愮ず璇嶅悕绉?
    description = Column(Text, default="")  # 鎻愮ず璇嶆弿杩?
    content = Column(Text, nullable=False)  # 鎻愮ず璇嶅唴瀹?
    is_active = Column(Boolean, default=True)  # 鏄惁鍚敤
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)

# 鏂板锛氱粯鍥鹃鏍兼ā鏉胯〃锛堝叏灞€閰嶇疆锛?
class StyleTemplate(Base):
    __tablename__ = "style_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)  # 妯℃澘鍚嶇О锛堝"鏃ユ极椋庢牸"锛?
    content = Column(Text, nullable=False)  # 椋庢牸鎻忚堪鍐呭
    scene_content = Column(Text, nullable=False, default="")  # 场景版本风格提示词
    prop_content = Column(Text, nullable=False, default="")  # 道具版本风格提示词
    is_default = Column(Boolean, default=False)  # 鏄惁涓洪粯璁ゆā鏉?
    created_at = Column(DateTime, default=datetime.utcnow)

# 瑙嗛椋庢牸鎻愮ず璇嶆ā鏉胯〃
class VideoStyleTemplate(Base):
    __tablename__ = "video_style_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)  # 妯℃澘鍚嶇О锛堝"3D锛堝崐鐪熷疄锛?銆?鐪熶汉"锛?
    sora_rule = Column(Text, nullable=False, default="")  # 鍑嗗垯閮ㄥ垎
    style_prompt = Column(Text, nullable=False, default="")  # 椋庢牸鎻忚堪閮ㄥ垎
    is_default = Column(Boolean, default=False)  # 鏄惁涓洪粯璁ゆā鏉?
    created_at = Column(DateTime, default=datetime.utcnow)

# 鏂板锛氬叏灞€閰嶇疆琛?
class GlobalSettings(Base):
    __tablename__ = "global_settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, nullable=False)  # 閰嶇疆閿紙濡?'sora_rule'锛?
    value = Column(Text, nullable=False)  # 閰嶇疆鍊?
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)


class BillingPriceRule(Base):
    __tablename__ = "billing_price_rules"

    id = Column(Integer, primary_key=True, index=True)
    rule_name = Column(String, nullable=False)
    category = Column(String, nullable=False, index=True)  # text/image/video
    stage = Column(String, default="", index=True)
    provider = Column(String, default="", index=True)
    model_name = Column(String, default="", index=True)
    resolution = Column(String, default="", index=True)
    billing_mode = Column(String, nullable=False)  # per_call/per_image/per_second
    unit_price_rmb = Column(Numeric(18, 5), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    priority = Column(Integer, default=0, nullable=False)
    effective_from = Column(DateTime, nullable=True)
    effective_to = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BillingLedgerEntry(Base):
    __tablename__ = "billing_ledger_entries"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    script_id = Column(Integer, nullable=False, index=True)
    episode_id = Column(Integer, nullable=False, index=True)
    shot_id = Column(Integer, nullable=True, index=True)
    storyboard2_shot_id = Column(Integer, nullable=True, index=True)
    sub_shot_id = Column(Integer, nullable=True, index=True)
    card_id = Column(Integer, nullable=True, index=True)
    dashboard_task_log_id = Column(Integer, nullable=True, index=True)
    category = Column(String, nullable=False, index=True)
    stage = Column(String, default="", index=True)
    provider = Column(String, default="", index=True)
    model_name = Column(String, default="", index=True)
    resolution = Column(String, default="", index=True)
    billing_mode = Column(String, nullable=False)
    quantity = Column(Numeric(18, 5), nullable=False, default=0)
    unit_price_rmb = Column(Numeric(18, 5), nullable=False, default=0)
    amount_rmb = Column(Numeric(18, 5), nullable=False, default=0)
    entry_type = Column(String, nullable=False, default="charge", index=True)  # charge/refund
    status = Column(String, nullable=False, default="finalized", index=True)  # pending/finalized/reversed
    billing_key = Column(String, nullable=False, unique=True, index=True)
    operation_key = Column(String, default="", index=True)
    attempt_index = Column(Integer, default=1, nullable=False)
    external_task_id = Column(String, default="", index=True)
    reason = Column(String, default="")
    detail_json = Column(Text, default="")
    parent_entry_id = Column(Integer, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

# 鏂板锛氬垎闀滃浘缁樺浘瑕佹眰妯℃澘琛?
class StoryboardRequirementTemplate(Base):
    __tablename__ = "storyboard_requirement_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)  # 妯℃澘鍚嶇О
    content = Column(Text, nullable=False)  # 妯℃澘鍐呭
    is_default = Column(Boolean, default=False)  # 鏄惁榛樿妯℃澘
    created_at = Column(DateTime, default=datetime.utcnow)

# 鏂板锛氬垎闀滃浘缁樼敾椋庢牸妯℃澘琛?
class StoryboardStyleTemplate(Base):
    __tablename__ = "storyboard_style_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)  # 妯℃澘鍚嶇О
    content = Column(Text, nullable=False)  # 妯℃澘鍐呭
    is_default = Column(Boolean, default=False)  # 鏄惁榛樿妯℃澘
    created_at = Column(DateTime, default=datetime.utcnow)

# 鏂板锛氭墭绠′細璇濊〃
class ManagedSession(Base):
    __tablename__ = "managed_sessions"

    id = Column(Integer, primary_key=True, index=True)
    episode_id = Column(Integer, ForeignKey("episodes.id"), nullable=False)
    status = Column(String, default="running")  # running/detached/completed/failed/stopped
    total_shots = Column(Integer, default=0)  # 鎬婚暅澶存暟
    completed_shots = Column(Integer, default=0)  # 宸插畬鎴愰暅澶存暟
    variant_count = Column(Integer, default=1)  # 每个原始镜头本次托管目标生成数量
    provider = Column(String, default="yijia")  # 鏈嶅姟鍟嗭細apimart/suchuang/yijia
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    # 鍏崇郴
    episode = relationship("Episode")
    tasks = relationship("ManagedTask", back_populates="session", cascade="all, delete-orphan")

# 鏂板锛氭墭绠′换鍔¤〃
class ManagedTask(Base):
    __tablename__ = "managed_tasks"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("managed_sessions.id"), nullable=False)
    shot_id = Column(Integer, ForeignKey("storyboard_shots.id"), nullable=False)  # 预留结果镜头槽位ID
    shot_stable_id = Column(String, nullable=False)  # 鍘熷闀滃ご鐨剆table_id
    video_path = Column(String, default="")  # 鐢熸垚鐨勮棰慍DN URL
    status = Column(String, default="pending")  # pending/processing/completed/failed
    error_message = Column(Text, default="")  # 澶辫触鍘熷洜
    task_id = Column(String, default="")  # 瑙嗛鐢熸垚浠诲姟ID
    prompt_text = Column(Text, default="")  # 本次任务实际提交的完整提示词
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    # 鍏崇郴
    session = relationship("ManagedSession", back_populates="tasks")
    shot = relationship("StoryboardShot")


class VoiceoverTtsTask(Base):
    __tablename__ = "voiceover_tts_tasks"

    id = Column(Integer, primary_key=True, index=True)
    episode_id = Column(Integer, ForeignKey("episodes.id"), nullable=False, index=True)
    line_id = Column(String, nullable=False, index=True)
    status = Column(String, default="pending", index=True)  # pending/processing/completed/failed
    request_json = Column(Text, default="")
    result_json = Column(Text, default="")
    error_message = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    episode = relationship("Episode")


class ShotDurationTemplate(Base):
    __tablename__ = "shot_duration_templates"

    id = Column(Integer, primary_key=True, index=True)
    duration = Column(Integer, nullable=False, unique=True)  # 6, 10, 15, 25
    shot_count_min = Column(Integer, nullable=False)  # 鏈€灏忓垎闀滅煭鍙ユ暟
    shot_count_max = Column(Integer, nullable=False)  # 鏈€澶у垎闀滅煭鍙ユ暟
    time_segments = Column(Integer, nullable=False)  # 鏃堕棿娈垫暟閲?
    simple_storyboard_rule = Column(Text, nullable=False)  # "绠€鍗曞垎闀滐細闀滃ご鍒掑垎"瀹屾暣鎻愮ず璇?
    simple_storyboard_config_json = Column(Text, default="")  # 简单分镜程序化规则配置
    video_prompt_rule = Column(Text, nullable=False)  # "鍒嗛暅瑙嗛鎻愮ず璇嶇敓鎴?瀹屾暣鎻愮ず璇?
    large_shot_prompt_rule = Column(Text, nullable=False, default="")
    is_default = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class VideoModelPricing(Base):
    __tablename__ = "video_model_pricing"

    id = Column(Integer, primary_key=True, index=True)
    provider = Column(String, nullable=False, index=True)  # yijia, apimart, suchuang
    model_name = Column(String, nullable=False, index=True)  # sora-2, sora-2-pro, grok
    duration = Column(Integer, nullable=False)  # 6, 10, 15, 25
    aspect_ratio = Column(String, nullable=False)  # 16:9, 9:16, 1:1, 2:3, 3:2
    price_yuan = Column(Float, nullable=False)  # Price in yuan
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class OpenRouterModel(Base):
    __tablename__ = "openrouter_models"

    id = Column(Integer, primary_key=True, index=True)
    model_id = Column(String, unique=True, nullable=False, index=True)  # e.g. "google/gemini-3-pro-preview"
    name = Column(String, nullable=False)                                # Display name
    context_length = Column(Integer, default=0)
    pricing_prompt = Column(String, default="0")                        # per-token price string
    pricing_completion = Column(String, default="0")
    modality = Column(String, default="")                               # e.g. "text->text"
    description = Column(Text, default="")
    synced_at = Column(DateTime, default=datetime.utcnow)


class RelayModel(Base):
    __tablename__ = "relay_models"

    id = Column(Integer, primary_key=True, index=True)
    model_id = Column(String, unique=True, nullable=False, index=True)
    owned_by = Column(String, default="")
    available_providers_count = Column(Integer, default=0)
    raw_metadata = Column(Text, default="")
    synced_at = Column(DateTime, default=datetime.utcnow, index=True)


class FunctionModelConfig(Base):
    __tablename__ = "function_model_configs"

    id = Column(Integer, primary_key=True, index=True)
    function_key = Column(String, unique=True, nullable=False, index=True)
    function_name = Column(String, nullable=False)          # Chinese display name
    provider_key = Column(String, nullable=True, default="openrouter")
    model_key = Column(String, nullable=True, default=None)
    model_id = Column(String, nullable=True, default=None)  # NULL = use default
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TextRelayTask(Base):
    __tablename__ = "text_relay_tasks"

    id = Column(Integer, primary_key=True, index=True)
    task_type = Column(String, nullable=False, index=True)
    owner_type = Column(String, nullable=False, default="", index=True)
    owner_id = Column(Integer, nullable=True, index=True)
    stage_key = Column(String, nullable=False, default="", index=True)
    function_key = Column(String, nullable=False, default="", index=True)
    model_id = Column(String, nullable=False, default="", index=True)
    external_task_id = Column(String, nullable=False, default="", index=True)
    poll_url = Column(Text, default="")
    status = Column(String, nullable=False, default="submitted", index=True)
    request_payload = Column(Text, default="")
    task_payload = Column(Text, default="")
    result_payload = Column(Text, default="")
    cost_rmb = Column(Numeric(18, 5), nullable=False, default=0)
    error_message = Column(Text, default="")
    billing_status = Column(String, nullable=False, default="pending", index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)
    completed_at = Column(DateTime, nullable=True, index=True)


# 爆款库相关表
class HitDrama(Base):
    __tablename__ = "hit_dramas"

    id = Column(Integer, primary_key=True, index=True)
    drama_name = Column(String, nullable=False)  # 剧名
    view_count = Column(String, default="")  # 播放量（文本类型）
    opening_15_sentences = Column(Text, default="")  # 开头15句
    first_episode_script = Column(Text, default="")  # 第一集文案
    online_time = Column(String, default="")  # 上线时间
    video_filename = Column(String, nullable=True)  # 视频文件名
    created_by = Column(String, nullable=False)  # 创建人用户名
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_deleted = Column(Boolean, default=False)  # 软删除标记

    # 关系
    edit_history = relationship("HitDramaEditHistory", back_populates="drama", cascade="all, delete-orphan")


class HitDramaEditHistory(Base):
    __tablename__ = "hit_drama_edit_history"

    id = Column(Integer, primary_key=True, index=True)
    drama_id = Column(Integer, ForeignKey("hit_dramas.id"), nullable=False, index=True)
    action_type = Column(String, nullable=False)  # create, update, delete
    field_name = Column(String, nullable=True)  # 修改的字段名，删除时为NULL
    old_value = Column(Text, nullable=True)  # 旧值
    new_value = Column(Text, nullable=True)  # 新值
    edited_by = Column(String, nullable=False)  # 编辑人用户名
    edited_at = Column(DateTime, default=datetime.utcnow, index=True)

    # 关系
    drama = relationship("HitDrama", back_populates="edit_history")

