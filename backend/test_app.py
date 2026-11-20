import pytest
from app import create_app, init_db

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('DEMO_PASSWORD','TestPassword123!')
    app=create_app({'TESTING':True,'SECRET_KEY':'test','DATABASE':str(tmp_path/'test.db')})
    init_db(app)
    return app.test_client()

def login(client,user='admin'):
    r=client.post('/api/login',json={'username':user,'password':'TestPassword123!'})
    assert r.status_code==200
    return {'X-CSRF-Token':r.json['csrf']}

def move(client,headers,kind,quantity):
    return client.post('/api/movements',headers=headers,json={'product_id':1,'kind':kind,'quantity':quantity,'note':'Test reference'})

def test_requires_login(client):
    assert client.get('/api/products').status_code==401

def test_bad_password(client):
    assert client.post('/api/login',json={'username':'admin','password':'wrong'}).status_code==401

def test_stock_lifecycle(client):
    h=login(client)
    assert move(client,h,'receipt',10).json['balance']==28
    assert move(client,h,'issue',5).json['balance']==23
    assert move(client,h,'return',2).json['balance']==25
    assert move(client,h,'adjustment',-3).json['balance']==22
    assert client.get('/api/movements').json[0]['balance']==22

def test_negative_stock_rolls_back(client):
    h=login(client)
    before=len(client.get('/api/movements').json)
    assert move(client,h,'issue',1000).status_code==400
    assert len(client.get('/api/movements').json)==before
    assert next(p for p in client.get('/api/products').json if p['id']==1)['quantity']==18

@pytest.mark.parametrize('qty',[0,-1,1.5,'bad',None])
def test_invalid_issue(client,qty):
    assert move(client,login(client),'issue',qty).status_code==400

def test_csrf(client):
    login(client)
    assert move(client,{},'receipt',1).status_code==403

def test_procurement_cannot_move(client):
    assert move(client,login(client,'procurement'),'receipt',1).status_code==403

def test_storekeeper_cannot_edit_rules(client):
    h=login(client,'storekeeper')
    assert client.put('/api/products/1/rules',headers=h,json={}).status_code==403
    assert client.get('/api/users').status_code==403

def test_reorder_calculation(client):
    h=login(client,'procurement')
    assert client.put('/api/products/1/rules',headers=h,json={'daily_demand':3.5,'lead_days':4,'safety_stock':5,'review_days':10}).status_code==200
    p=next(p for p in client.get('/api/products').json if p['id']==1)
    assert (p['reorder_point'],p['target_stock'],p['suggested_order'])==(19,54,36)
    assert p['status']=='Low stock'

def test_csv_and_filter(client):
    h=login(client)
    client.post('/api/movements',headers=h,json={'product_id':1,'kind':'receipt','quantity':1,'note':'=1+1'})
    result=client.get('/api/reports.csv')
    assert result.status_code==200 and "'=1+1" in result.text
    assert client.get('/api/movements?start=2999-01-01').json==[]

def test_create_user_and_product(client):
    h=login(client)
    assert client.post('/api/users',headers=h,json={'username':'new','name':'New member','password':'LongPassword123','role':'Storekeeper'}).status_code==201
    p={'sku':'UNIQUE','name':'New product','category':'Office','warehouse':'Store','unit_cost':12}
    assert client.post('/api/products',headers=h,json=p).status_code==201
    assert client.post('/api/products',headers=h,json=p).status_code==409

def test_logout(client):
    h=login(client)
    assert client.post('/api/logout',headers=h).status_code==200
    assert client.get('/api/products').status_code==401
