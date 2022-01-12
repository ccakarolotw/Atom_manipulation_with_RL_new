from Environment.Env import RealExpEnv
from Environment.get_atom_coordinate import get_atom_coordinate_nm, get_all_atom_coordinate_nm, get_atom_coordinate_nm_with_anchor

import numpy as np
import scipy.spatial as spatial
from scipy.optimize import linear_sum_assignment

def circle(x, y, r, p = 100):
    x_, y_ = [], []
    for i in range(p):
        x_.append(x+r*np.cos(2*i*np.pi/p))
        y_.append(y+r*np.sin(2*i*np.pi/p))
    return x_, y_ 

def assignment(start, goal):
    cost_matrix = spatial.distance.cdist(np.array(start)[:,:2], np.array(goal)[:,:2])
    row_ind, col_ind = linear_sum_assignment(cost_matrix)
    cost = cost_matrix[row_ind, col_ind]
    total_cost = np.sum(cost)
    return np.array(start)[row_ind,:], np.array(goal)[col_ind,:], cost, total_cost, row_ind, col_ind

def align_design(atoms, design):
    assert atoms.shape == design.shape
    c_min = np.inf
    for i in range(atoms.shape[0]):
        for j in range(design.shape[0]):
            a = atoms[i,:]
            d = design[j,:]
            design_ = design+a-d
            a_index = np.delete(np.arange(atoms.shape[0]), i)
            d_index = np.delete(np.arange(design.shape[0]), j)
            a, d, _, c, _, _ = assignment(atoms[a_index,:], design_[d_index,:])
            if (c<c_min):
                c_min = c
                atoms_assigned, design_assigned = a, d
    return atoms_assigned, design_assigned, c_min, atoms[i,:]

def get_atom_and_anchor(all_atom_absolute_nm, anchor_nm):
    new_anchor_nm, anchor_nm, _, _, row_ind, col_ind = assignment(all_atom_absolute_nm, anchor_nm)
    atoms_nm = np.delete(all_atom_absolute_nm, row_ind, axis=0)
    return atoms_nm, new_anchor_nm

def get_anchor(atom, anchors):
    if anchors.shape[0]==1:
        anchor= anchors[0,:]
    else:
        anchor, _, _, _, _, _ = assignment(anchors, atom.reshape((-1,2)))
    return anchor


class Structure_Builder(RealExpEnv):
    def __init__(self, step_nm, max_mvolt, max_pcurrent_to_mvolt_ratio, goal_nm, current_jump, im_size_nm, offset_nm,
                 pixel, scan_mV, max_len):
        super(Structure_Builder, self).__init__(step_nm, max_mvolt, max_pcurrent_to_mvolt_ratio, goal_nm, None, current_jump, im_size_nm, offset_nm,
                 None, pixel, None, None, scan_mV, max_len)
        self.atom_absolute_nm_f = None
        self.atom_absolute_nm_b = None
        self.large_DX_DDeltaX = float(self.createc_controller.stm.getparam('DX/DDeltaX'))
        self.large_offset_nm = offset_nm
        self.large_len_nm = im_size_nm

    def reset_large(self, design_nm):
        
        self.num_atoms = design_nm.shape[0]
        self.all_atom_absolute_nm = self.scan_all_atoms(self.large_offset_nm, self.large_len_nm) 
        self.atoms, self.designs, c_min, anchor = align_design(self.all_atom_absolute_nm, design_nm)
        self.design_nm = np.concatenate((self.designs, anchor))
        self.large_img_info |= {'design': self.design_nm}
        self.init_anchor = anchor
        self.anchors = [self.init_anchor]
        self.atom_chosen, self.design_chosen = self.match_atoms_designs()
        self.anchor_chosen = self.init_anchor
        offset_nm, len_nm = self.get_offset_len()
        return self.atom_chosen, self.design_chosen, self.anchor_chosen, offset_nm, len_nm
        
    def step_large(self, succeed, new_atom_position):
        self.all_atom_absolute_nm = self.scan_all_atoms(self.large_offset_nm, self.large_len_nm)
        self.large_img_info |= {'design': self.design_nm}
        self.atoms = get_atom_and_anchor(self.all_atom_absolute_nm, np.vstack(self.anchors))
        if succeed:
            self.update_after_success(new_atom_position)
        self.atom_chosen, self.design_chosen = self.match_atoms_designs()
        self.anchor_chosen = get_anchor(self.atom_chosen, np.vstack(self.anchors))
        offset_nm, len_nm = self.get_offset_len()
        return self.atom_chosen, self.design_chosen, self.anchor_chosen, offset_nm, len_nm

    def get_offset_len(self):
        len_nm = 2*max(np.max(np.abs(self.anchor_chosen - self.atom_chosen)), 2)+2
        offset_nm = self.atom_chosen +np.array([0,-0.5*len_nm])
        return offset_nm, len_nm


    def update_after_success(self, new_atom_position):
        i = np.argmin(spatial.cdist(self.all_atom_absolute_nm, new_atom_position.reshape((-1,2))))
        new_atom_position = self.all_atom_absolute_nm[i,:]
        self.atoms = np.delete(self.atoms, (self.atoms == new_atom_position).all(axis=1).nonzero())
        self.designs = np.delete(self.designs, (self.designs == self.design_chosen).all(axis=1).nonzero())
        self.anchors.append(new_atom_position)

    def match_atoms_designs(self):
        atoms, designs, costs, _, _, _ = assignment(self.atoms, self.designs)
        j = np.argmax(costs)
        atom_chosen = atoms[j,:]
        design_chosen = designs[j,:]
        return atom_chosen, design_chosen   

    def reset(self, destination_nm, anchor_nm, offset_nm, len_nm, large_len_nm):
        self.len = 0
        self.atom_absolute_nm, self.anchor_nm = self.scan_atom(anchor_nm, offset_nm, len_nm, large_len_nm)

        self.atom_start_absolute_nm = self.atom_absolute_nm
        destination_nm_with_correction = destination_nm + self.anchor_nm - anchor_nm
        self.destination_absolute_nm, self.goal = self.get_destination(self.atom_start_absolute_nm, destination_nm_with_correction)
        info = {'start_absolute_nm':self.atom_start_absolute_nm, 'goal_absolute_nm':self.destination_absolute_nm,
                'start_absolute_nm_f':self.atom_absolute_nm_f, 'start_absolute_nm_b':self.atom_absolute_nm_b, 'img_info':self.img_info}
        return np.concatenate((self.goal, (self.atom_absolute_nm - self.atom_start_absolute_nm)/self.goal_nm)), info
    
    def step(self, action):
        x_start_nm , y_start_nm, x_end_nm, y_end_nm, mvolt, pcurrent = self.action_to_latman_input(action)
        current_series, d = self.step_latman(x_start_nm , y_start_nm, x_end_nm, y_end_nm, mvolt, pcurrent)
        info = {'current_series':current_series, 'd': d, 'start_nm':  np.array([x_start_nm , y_start_nm]), 'end_nm':np.array([x_end_nm , y_end_nm])}

        done = False
        self.len+=1
        done = self.len == self.max_len
        if not done:
            jump = self.detect_current_jump(current_series)

        if done or jump:
            self.dist_destination, dist_start, dist_last = self.check_similarity()
            print('atom moves by:', dist_start)
            done = done or (dist_start > 1.5*self.goal_nm) or (self.dist_destination < self.precision_lim)
            self.atom_move_detector.push(current_series, dist_last)

        next_state = np.concatenate((self.goal, (self.atom_absolute_nm -self.atom_start_absolute_nm)/self.goal_nm))
        info |= {'atom_absolute_nm':self.atom_absolute_nm, 'atom_absolute_nm_f' : self.atom_absolute_nm_f, 'atom_absolute_nm_b' : self.atom_absolute_nm_b, 'img_info' : self.img_info}
        
        return next_state, None, done, info

    def scan_atom(self, anchor_nm = None, offset_nm = None, len_nm = None, large_len_nm = None):
        if offset_nm is not None:
            self.offset_nm = offset_nm 
        if len_nm is not None:
            self.len_nm = len_nm 
        if anchor_nm is not None:
            self.anchor_nm = anchor_nm 
        if large_len_nm is not None:
            small_DX_DDeltaX = int(self.large_DX_DDeltaX*len_nm/large_len_nm)
            self.createc_controller.stm.setparam('DX/DDeltaX', small_DX_DDeltaX)

        self.createc_controller.offset_nm = self.offset_nm
        self.createc_controller.im_size_nm = self.len_nm
        
        img_forward, img_backward, offset_nm, len_nm = self.createc_controller.scan_image()
        self.img_info = {'img_forward':img_forward,'img_backward':img_backward, 'offset_nm':offset_nm, 'len_nm':len_nm}
        self.atom_absolute_nm_f, self.anchor_nm_f = get_atom_coordinate_nm_with_anchor(img_forward, offset_nm, len_nm, self.anchor_nm)
        self.atom_absolute_nm_b, self.anchor_nm_b = get_atom_coordinate_nm_with_anchor(img_backward, offset_nm, len_nm, self.anchor_nm)
        self.atom_absolute_nm = 0.5*(self.atom_absolute_nm_f+self.atom_absolute_nm_b)
        self.anchor_nm = 0.5*(self.anchor_nm_f+self.anchor_nm_b)
        return self.atom_absolute_nm, self.anchor_nm

    def scan_all_atoms(self, offset_nm, len_nm):
        self.createc_controller.stm.setparam('DX/DDeltaX', self.large_DX_DDeltaX)
        self.createc_controller.offset_nm = offset_nm
        self.createc_controller.im_size_nm = len_nm
        self.offset_nm = offset_nm
        self.len_nm = len_nm
        img_forward, img_backward, offset_nm, len_nm = self.createc_controller.scan_image()
        all_atom_absolute_nm_f = get_all_atom_coordinate_nm(img_forward, offset_nm, len_nm)
        all_atom_absolute_nm_b = get_all_atom_coordinate_nm(img_backward, offset_nm, len_nm)

        all_atom_absolute_nm_f = np.array(sorted(all_atom_absolute_nm_f, key = lambda x: (x[0], x[1])))
        all_atom_absolute_nm_b = np.array(sorted(all_atom_absolute_nm_b, key = lambda x: (x[0], x[1])))

        self.all_atom_absolute_nm_f = all_atom_absolute_nm_f
        self.all_atom_absolute_nm_b = all_atom_absolute_nm_b

        if len(all_atom_absolute_nm_b)!=len(all_atom_absolute_nm_f):
            print('length of list of atoms found in b and f different')

        all_atom_absolute_nm = 0.5*(all_atom_absolute_nm_f+all_atom_absolute_nm_b)
        self.all_atom_absolute_nm = all_atom_absolute_nm
        self.large_img_info = {'img_forward':img_forward,'img_backward':img_backward, 'offset_nm':offset_nm, 'len_nm':len_nm,
                                'all_atom_absolute_nm':all_atom_absolute_nm, 'all_atom_absolute_nm_f':all_atom_absolute_nm_f,
                                'all_atom_absolute_nm_b':all_atom_absolute_nm_b}
        return all_atom_absolute_nm

    def get_destination(self, atom_start_absolute_nm, destination_absolute_nm):
        angle = np.arctan2((destination_absolute_nm-atom_start_absolute_nm)[1],(destination_absolute_nm-atom_start_absolute_nm)[0])
        goal_nm = min(self.goal_nm, np.linalg.norm(destination_absolute_nm-atom_start_absolute_nm))
        dr = goal_nm*np.array([np.cos(angle),np.sin(angle)])
        destination_absolute_nm = atom_start_absolute_nm + dr
        return destination_absolute_nm, dr/self.goal_nm

    def step_latman(self, x_start_nm, y_start_nm, x_end_nm, y_end_nm, mvoltage, pcurrent):
        x_start_nm+=self.atom_absolute_nm[0]
        x_end_nm+=self.atom_absolute_nm[0]
        y_start_nm+=self.atom_absolute_nm[1]
        y_end_nm+=self.atom_absolute_nm[1]
        if [x_start_nm, y_start_nm] != [x_end_nm, y_end_nm]:
            data = self.createc_controller.lat_manipulation(x_start_nm, y_start_nm, x_end_nm, y_end_nm, mvoltage, pcurrent, self.offset_nm, self.len_nm)
            if data is not None:
                current = np.array(data.current).flatten()
                x = np.array(data.x)
                y = np.array(data.y)
                d = np.sqrt(((x-x[0])**2 + (y-y[0])**2))
            else:
                current = None
                d = None
            return current, d
        else:
            return None, None