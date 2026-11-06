import re
def reference_policy(r):
 t=r['title'];b=r.get('body','');flat=' '.join(b.split())
 if not b.strip():return None
 rules=[
 ('software_purchase',r'(?:Adobe|어도비|소프트웨어|라이선스).{0,35}(?:공동구매|구입|구매)',r'가격|라이선스|구매|원/년','해당없음'),
 ('degree_examination',r'학위.{0,15}(?:논문|청구)|논문.{0,10}(?:심사|발표)',r'심사요구서|심사료|학위논문 제출|논문심사|심사용 논문','해당없음'),
 ('academic_advising',r'지도교수\s*상담|지도교수상담제|PASS Program',r'상담|성적|지도','해당없음'),
 ('formal_curriculum',r'교육과정.{0,20}(?:개정|신설|명칭변경|이수학기)',r'교과목|학점|교육과정','해당없음'),
 ('academic_major',r'(?:복수전공|부전공).{0,15}(?:이수자|선발|신청)',r'학점|학적|이수','해당없음'),
 ('curricular_registration',r'(?:교과목|기초수학).{0,25}수강신청|기초수학.{0,15}수강\s*안내',r'학점|교과목|교육과정','해당없음'),
 ('guide_distribution',r'가이드북.{0,12}(?:안내|배포)|자료집.{0,12}(?:공유|배포)',r'배부|배포|자료|가이드북','해당없음'),
 ('service_survey',r'구독\s*수요조사|만족도\s*조사|설문\s*조사',r'응답|설문|조사기간|조사내용','해당없음'),
 ('proposal_contest',r'비교과\s*프로그램.{0,12}공모전|교과목.{0,12}공모전',r'공모전\s*제안서|프로그램\s*제안서|강의\s*계획서|수업\s*계획서','[공모전] 아이디어/기획'),
 ('tutor_job',r'^과외(?:\s|[(]|$)',r'선생님|주\s*\d.{0,5}회\s*수업','취업 정보'),
 ('mentoring_volunteer',r'멘토\s*(?:추가\s*)?모집',r'진로\s*상담\s*및\s*멘토링|고등학생.{0,40}(?:상담|멘토링)|학습\s*지도','봉사활동'),
 ]
 for name,tp,bp,label in rules:
  if re.search(tp,t,re.I) and (m:=re.search(bp,flat,re.I)):
   if name=='degree_examination' and re.search(r'작성법|작성\s*특강|작성\s*교육|세미나',t):continue
   if name=='mentoring_volunteer' and re.search(r'근로계약|월급|시급|근무시간|채용',t+' '+flat[:600]):continue
   return dict(label=label,rule=name,evidence=flat[max(0,m.start()-45):m.end()+100],title=t)
 return None
